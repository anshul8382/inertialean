#!/usr/bin/env python3
"""
Create Service Ticket Table Migration Script
Run this script to create the service_ticket table and default SLA configurations
"""

import os
import sys
from sqlalchemy import text

# Add the app directory to the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from main import create_app
from extensions import db
from alert_system_models import SLAConfiguration

def create_ticket_table():
    """Create service ticket table and SLA configurations"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if table already exists
            result = db.session.execute(text("SHOW TABLES LIKE 'service_ticket'"))
            if result.fetchone():
                print("⚠️  service_ticket table already exists. Skipping table creation.")
                # Best-effort: add snooze fields if missing (backwards compatible upgrade)
                try:
                    col_check = db.session.execute(text("SHOW COLUMNS FROM service_ticket LIKE 'snoozed_until'"))
                    if not col_check.fetchone():
                        print("ℹ️  Adding snooze fields to existing service_ticket table...")
                        db.session.execute(text("""
                            ALTER TABLE `service_ticket`
                              ADD COLUMN `pre_snooze_status` varchar(20) DEFAULT NULL,
                              ADD COLUMN `snoozed_until` datetime DEFAULT NULL,
                              ADD COLUMN `snoozed_at` datetime DEFAULT NULL,
                              ADD COLUMN `snoozed_by` int(11) DEFAULT NULL,
                              ADD COLUMN `snooze_reason` text DEFAULT NULL;
                        """))
                        db.session.execute(text("ALTER TABLE `service_ticket` ADD KEY `snoozed_by` (`snoozed_by`);"))
                        db.session.execute(text("""
                            ALTER TABLE `service_ticket`
                              ADD CONSTRAINT `service_ticket_ibfk_5`
                              FOREIGN KEY (`snoozed_by`) REFERENCES `user` (`id`) ON DELETE SET NULL;
                        """))
                        db.session.commit()
                        print("✅ Snooze fields added successfully")
                except Exception as e:
                    db.session.rollback()
                    print(f"⚠️  Could not add snooze fields automatically: {str(e)}")
            else:
                # Create table
                create_table_sql = """
                CREATE TABLE `service_ticket` (
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
                """
                db.session.execute(text(create_table_sql))
                db.session.commit()
                print("✅ service_ticket table created successfully")
            
            # Add default SLA configurations for service tickets
            ticket_configs = [
                {
                    'process_name': 'service_ticket',
                    'process_stage': 'critical',
                    'sla_hours': 4,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'service_ticket',
                    'process_stage': 'high',
                    'sla_hours': 24,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'service_ticket',
                    'process_stage': 'medium',
                    'sla_hours': 72,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'service_ticket',
                    'process_stage': 'low',
                    'sla_hours': 168,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                }
            ]
            
            configs_added = 0
            for config_data in ticket_configs:
                existing = SLAConfiguration.query.filter_by(
                    process_name=config_data['process_name'],
                    process_stage=config_data['process_stage']
                ).first()
                if not existing:
                    config = SLAConfiguration(**config_data)
                    db.session.add(config)
                    configs_added += 1
            
            if configs_added > 0:
                db.session.commit()
                print(f"✅ Added {configs_added} SLA configurations for service tickets")
            else:
                print("ℹ️  SLA configurations for service tickets already exist")
            
            print("✅ Migration completed successfully!")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error during migration: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    return True

if __name__ == "__main__":
    create_ticket_table()

