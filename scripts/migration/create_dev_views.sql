-- Create missing views in development database
USE inertia_app2025_dev;

-- Create active_alerts_view
CREATE OR REPLACE VIEW active_alerts_view AS 
SELECT 
    a.id,
    a.alert_type,
    a.alert_subtype,
    a.severity,
    a.status,
    a.client_id,
    a.workflow_id,
    a.recommendation_id,
    a.user_id,
    a.title,
    a.description,
    a.sla_timeline,
    a.current_delay,
    a.created_at,
    a.acknowledged_at,
    a.resolved_at,
    a.snoozed_until,
    a.escalated_at,
    a.escalated_to,
    a.resolved_by,
    a.resolution_notes,
    c.name AS client_name,
    u.username AS assigned_user_name,
    w.current_stage AS workflow_stage
FROM alert a
LEFT JOIN client c ON a.client_id = c.id
LEFT JOIN user u ON a.user_id = u.id
LEFT JOIN workflow w ON a.workflow_id = w.id
WHERE a.status = 'active'
ORDER BY a.created_at DESC;

-- Create alert_summary_view
CREATE OR REPLACE VIEW alert_summary_view AS 
SELECT 
    alert.alert_type,
    alert.severity,
    COUNT(*) AS count,
    AVG(TIMESTAMPDIFF(HOUR, alert.created_at, CURRENT_TIMESTAMP())) AS avg_age_hours
FROM alert 
WHERE alert.status = 'active'
GROUP BY alert.alert_type, alert.severity;
