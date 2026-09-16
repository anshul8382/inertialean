"""
Intelligence Hub AI decision service.
Analyzes open DataIntegrityIssue records using local Ollama.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

from services.ollama_service import OllamaUnavailableError, ollama_service

logger = logging.getLogger(__name__)

# Backward-compatible alias for intelligence hub routes
HubAIUnavailableError = OllamaUnavailableError


class HubAIService:
    def _get_model(self) -> str:
        return ollama_service.get_model()

    def _call_ollama(self, prompt: str, max_tokens: int = 1200) -> str:
        return ollama_service.generate(prompt, max_tokens=max_tokens, temperature=0.1)

    def _get_market_context(self, has_ppm_issues: bool) -> str:
        if not has_ppm_issues:
            return ""
        try:
            from flask import current_app

            api_key = current_app.config.get("PERPLEXITY_API_KEY", "")
            if not api_key:
                return ""
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": "llama-3.1-sonar-small-128k-online",
                "messages": [
                    {
                        "role": "user",
                        "content": "In 2 sentences, what is the current Nifty 50 market trend and whether Indian equity markets are in a bull or bear phase right now? Be factual and brief.",
                    }
                ],
                "max_tokens": 120,
                "temperature": 0.1,
            }
            resp = requests.post(
                "https://api.perplexity.ai/chat/completions", headers=headers, json=payload, timeout=15
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("Perplexity market context failed: %s", exc)
        return ""

    def _get_past_feedback(self) -> str:
        try:
            from models import AgentFeedback

            feedbacks = (
                AgentFeedback.query.filter_by(status="addressed").order_by(AgentFeedback.addressed_at.desc()).limit(5).all()
            )
            if not feedbacks:
                return ""
            lines = []
            for fb in feedbacks:
                lines.append(
                    f"- [{fb.category}] {fb.title or ''}: {(fb.description or '')[:100]} -> resolved as: {(fb.response or 'n/a')[:80]}"
                )
            return "\n".join(lines)
        except Exception:
            return ""

    def _build_prompt(self, issues: list, client_contexts: dict, past_feedback: str, market_context: str) -> str:
        lines = [
            "You are an investment operations assistant. Analyze these issues and recommend the best action for each. Reply ONLY with a valid JSON array - no explanation, no markdown, just the array.\n"
        ]
        if market_context:
            lines.append(f"MARKET BACKGROUND:\n{market_context}\n")
        if client_contexts:
            lines.append(
                "CLIENTS:\n"
                + "\n".join(
                    [
                        f"- {ctx['name']} | AUM INR {ctx.get('aum', 0):.1f}Cr | RM: {ctx.get('rm', 'Unknown')} | {ctx.get('open_issues', 0)} open issues"
                        for _, ctx in client_contexts.items()
                    ]
                )
                + "\n"
            )
        if past_feedback:
            lines.append(f"PAST RESOLUTIONS:\n{past_feedback}\n")

        issue_lines = []
        for i, issue in enumerate(issues, 1):
            client_name = client_contexts.get(issue.get("client_id", 0), {}).get("name", "Unknown")
            issue_lines.append(
                f'{i}. id:{issue["id"]} client:"{client_name}" category:{issue["check_category"]} check:{issue["check_name"]} severity:{issue["severity"]} age:{issue["age_days"]}d message:"{issue["message"][:100]}"'
            )
        lines.append("ISSUES TO ANALYZE:\n" + "\n".join(issue_lines) + "\n")
        lines.append(
            "ROLES: advisor=client work & trades | manager=escalations & systemic issues | admin=data/system\n"
            "ACTIONS: create_task | create_ticket | create_alert | resolve | monitor\n"
            'Return: [{"issue_id": 123, "action": "create_task", "priority": "high", "assignee_role": "advisor", "title": "...", "description": "...", "reason": "..."}]\n'
            "JSON array only:"
        )
        return "\n".join(lines)

    def _parse_response(self, raw: str) -> list:
        raw = raw.strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            return []
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return []

        results = []
        for item in data:
            if not isinstance(item, dict):
                continue
            action = item.get("action", "monitor")
            priority = item.get("priority", "medium")
            assignee_role = item.get("assignee_role", "advisor")
            if action not in ("create_task", "create_ticket", "create_alert", "resolve", "monitor"):
                action = "monitor"
            if priority not in ("high", "medium", "low"):
                priority = "medium"
            if assignee_role not in ("advisor", "manager", "admin"):
                assignee_role = "advisor"
            results.append(
                {
                    "issue_id": item.get("issue_id"),
                    "action": action,
                    "priority": priority,
                    "assignee_role": assignee_role,
                    "title": str(item.get("title", ""))[:200],
                    "description": str(item.get("description", ""))[:1000],
                    "reason": str(item.get("reason", ""))[:500],
                    "assignee_user_id": None,
                }
            )
        return results

    def _resolve_assignee_user(self, role: str, client_id: Optional[int], issue_category: str) -> Optional[int]:
        try:
            from services.task_assignment_service import get_assignee_for_alert

            assignee = get_assignee_for_alert(
                alert_type="data_integrity", alert_subtype=issue_category, client_id=client_id
            )
            if assignee:
                return assignee
        except Exception:
            pass

        try:
            from models import User

            if role == "admin":
                user = User.query.filter_by(is_admin=True, is_active=True).first()
            elif role == "manager":
                user = User.query.filter(User.is_active.is_(True)).filter(
                    (User.role == "manager") | (User.is_admin.is_(True))
                ).first()
            else:
                user = User.query.filter_by(is_active=True).first()
            return user.id if user else None
        except Exception:
            return None

    def analyze_issues(self, issue_ids: list, user_id: int) -> list:
        from models import Client, DataIntegrityIssue

        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.id.in_(issue_ids), DataIntegrityIssue.status.in_(["open", "baseline"])
        ).all()
        if not issues:
            return []

        self._get_model()
        client_ids = list(set(i.client_id for i in issues))
        clients = {c.id: c for c in Client.query.filter(Client.id.in_(client_ids)).all()}
        client_contexts = {}
        for cid in client_ids:
            c = clients.get(cid)
            if c:
                client_contexts[cid] = {
                    "name": c.name,
                    "aum": float(getattr(c, "aum", 0) or 0) / 1e7,
                    "rm": getattr(c, "relationship_manager", None) or "Unknown",
                    "open_issues": sum(1 for i in issues if i.client_id == cid),
                }

        has_ppm = any(i.check_category == "PORTFOLIO_PERFORMANCE" for i in issues)
        market_context = self._get_market_context(has_ppm)
        past_feedback = self._get_past_feedback()
        now = datetime.utcnow()
        issue_dicts = []
        for issue in issues:
            age_days = (now - issue.detected_at).days if issue.detected_at else 0
            issue_dicts.append(
                {
                    "id": issue.id,
                    "client_id": issue.client_id,
                    "check_category": issue.check_category,
                    "check_name": issue.check_name,
                    "severity": issue.severity,
                    "message": issue.message or "",
                    "age_days": age_days,
                }
            )

        raw_response = self._call_ollama(self._build_prompt(issue_dicts, client_contexts, past_feedback, market_context))
        recommendations = self._parse_response(raw_response)
        issue_map = {i.id: i for i in issues}
        for rec in recommendations:
            issue = issue_map.get(rec["issue_id"])
            if issue:
                rec["client_id"] = issue.client_id
                rec["client_name"] = client_contexts.get(issue.client_id, {}).get("name", "Unknown")
                rec["check_category"] = issue.check_category
                rec["severity"] = issue.severity
                rec["assignee_user_id"] = self._resolve_assignee_user(rec["assignee_role"], issue.client_id, issue.check_category)
                if rec["action"] in ("create_task", "create_ticket"):
                    days = 2 if issue.severity == "critical" else 5
                    rec["suggested_deadline"] = (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")
                else:
                    rec["suggested_deadline"] = None
        return recommendations

    def execute_recommendation(self, rec: dict, user_id: int) -> dict:
        from flask import url_for

        from extensions import db

        action = rec.get("action")
        client_id = rec.get("client_id")
        issue_id = rec.get("issue_id")
        priority = rec.get("priority", "medium")
        title = rec.get("title", "AI-recommended action")
        description = rec.get("description", "")
        assignee_user_id = rec.get("assignee_user_id") or user_id
        deadline_str = rec.get("suggested_deadline")

        if action == "create_task":
            from models import OpsTask

            deadline = datetime.fromisoformat(deadline_str) if deadline_str else datetime.utcnow() + timedelta(days=3)
            task = OpsTask(
                name=title,
                deadline=deadline,
                assigned_to=assignee_user_id,
                client_id=client_id,
                data_integrity_issue_id=issue_id,
                priority=priority,
                notes=f"AI-recommended: {description}\n\nReason: {rec.get('reason', '')}",
                created_by=user_id,
                status="pending",
            )
            db.session.add(task)
            db.session.commit()
            try:
                from services.google_calendar_service import sync_ops_task_google_calendar
                from services.google_tasks_service import sync_ops_task_google_tasks

                sync_ops_task_google_calendar(task.id)
                sync_ops_task_google_tasks(task.id)
            except Exception:
                pass
            return {"success": True, "type": "task", "id": task.id, "url": url_for("tasks.view_task", task_id=task.id)}

        if action == "create_ticket":
            from models import ServiceTicket

            date_str = datetime.utcnow().strftime("%Y%m%d")
            count = ServiceTicket.query.filter(ServiceTicket.ticket_number.like(f"TKT-{date_str}-%")).count()
            ticket_number = f"TKT-{date_str}-{count + 1:04d}"
            sla_hours = 4 if priority == "high" else 72
            ticket = ServiceTicket(
                ticket_number=ticket_number,
                client_id=client_id,
                title=title,
                description=description,
                alert_type="data_integrity",
                priority=priority,
                sla_hours=sla_hours,
                sla_deadline=datetime.utcnow() + timedelta(hours=sla_hours),
                assigned_to=assignee_user_id,
                created_by=user_id,
            )
            db.session.add(ticket)
            db.session.commit()
            return {
                "success": True,
                "type": "ticket",
                "id": ticket.id,
                "url": url_for("tickets.view_ticket", ticket_id=ticket.id),
            }

        if action == "create_alert":
            from alert_system_models import Alert

            severity_map = {"high": "critical", "medium": "warning", "low": "info"}
            alert = Alert(
                alert_type="data_integrity",
                alert_subtype=rec.get("check_category", "general").lower(),
                severity=severity_map.get(priority, "warning"),
                status="active",
                client_id=client_id,
                user_id=assignee_user_id,
                title=title,
                description=description,
            )
            db.session.add(alert)
            db.session.commit()
            return {"success": True, "type": "alert", "id": alert.id, "url": url_for("alerts.view_alert", alert_id=alert.id)}

        if action == "resolve":
            from models import DataIntegrityIssue

            issue = DataIntegrityIssue.query.get(issue_id)
            if issue:
                issue.status = "resolved"
                issue.resolution_type = "fixed"
                issue.resolved_at = datetime.utcnow()
                issue.resolved_by = user_id
                issue.resolution_notes = f"AI-resolved: {rec.get('reason', '')}"
                db.session.commit()
            return {"success": True, "type": "resolved", "id": issue_id, "url": None}

        return {"success": True, "type": "monitor", "id": issue_id, "url": None}


hub_ai_service = HubAIService()
