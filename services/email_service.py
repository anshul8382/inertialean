#!/usr/bin/env python3
"""
Email Service
Email generation and sending for recommendations
"""

from typing import Dict, List, Tuple, Optional
from datetime import datetime
from sqlalchemy.orm import joinedload
from models import Client, Recommendation, RecommendationSession, Security
from services.recommendation_formatter import RecommendationFormatter
from extensions import db, mail
from flask import render_template
from flask_mail import Message, sanitize_addresses
import logging
import html as _html

logger = logging.getLogger(__name__)


class EmailService:
    """Email generation and sending service"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.formatter = RecommendationFormatter()

    @staticmethod
    def partition_recommendations_for_email(formatted: List[Dict]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        Split formatted rows into all stock rows, BUY list, SELL list (uppercase action; no Jinja selectattr).
        """
        stock: List[Dict] = [r for r in (formatted or []) if r.get('type') != 'header']
        buy_recs: List[Dict] = []
        sell_recs: List[Dict] = []
        for r in stock:
            a = str(r.get('action') or '').strip().upper()
            if a == 'SELL':
                sell_recs.append(r)
            elif a == 'BUY':
                buy_recs.append(r)
            else:
                amt = float(r.get('amount') or 0)
                qty = int(r.get('quantity') or 0)
                if amt > 0 or qty > 0:
                    buy_recs.append(r)
        return stock, buy_recs, sell_recs

    @staticmethod
    def _na(value, default: str = "N/A") -> str:
        """Return a safe string for missing values (never blank)."""
        if value is None:
            return default
        s = str(value).strip()
        return s if s else default

    @staticmethod
    def _fmt_num(value, decimals: int = 0, na: str = "N/A") -> str:
        try:
            if value is None:
                return na
            f = float(value)
            if decimals <= 0:
                return f"{int(round(f)):,}"
            return f"{f:,.{decimals}f}"
        except Exception:
            return na

    @staticmethod
    def _ascii_table(headers, rows, right_align_cols=None) -> str:
        right_align_cols = set(right_align_cols or [])

        # Convert everything to strings (no blanks)
        str_rows = []
        for r in rows:
            str_rows.append([EmailService._na(c, default="N/A") for c in r])

        widths = []
        for i, h in enumerate(headers):
            w = len(str(h))
            for r in str_rows:
                w = max(w, len(r[i]) if i < len(r) else 0)
            widths.append(min(w, 44))  # cap to avoid absurdly wide tables

        def _clip(s: str, w: int) -> str:
            if len(s) <= w:
                return s
            if w <= 1:
                return s[:w]
            return s[: w - 1] + "…"

        def _row(vals):
            parts = []
            for i, v in enumerate(vals):
                cell = _clip(v, widths[i])
                if i in right_align_cols:
                    parts.append(cell.rjust(widths[i]))
                else:
                    parts.append(cell.ljust(widths[i]))
            return "| " + " | ".join(parts) + " |"

        sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
        out = [sep, _row([str(h) for h in headers]), sep]
        for r in str_rows:
            out.append(_row(r))
        out.append(sep)
        return "\n".join(out)

    def generate_plain_text_draft(
        self,
        *,
        client: Client,
        recommendations: List[Dict],
        custom_message: str,
        current_month: str,
        total_value: float,
        buy_total: float,
        sell_total: float,
    ) -> str:
        """
        Create a plain-text email draft (simple datapoints + ASCII table).
        Ensures no missing/blank values appear (uses 'N/A').
        """
        _, buy_recs, sell_recs = EmailService.partition_recommendations_for_email(recommendations)
        net_total = float(buy_total or 0) - float(sell_total or 0)

        lines: List[str] = []
        cm = (custom_message or "").strip()
        if cm:
            lines.append(cm)
            lines.append("")

        lines.append(f"Portfolio Investment Recos for {self._na(getattr(client, 'name', None))} {self._na(current_month)}")
        lines.append("")
        lines.append("Quick summary")
        lines.append(f"- Current portfolio value: ₹{self._fmt_num(total_value, 0)}")
        if buy_recs:
            lines.append(f"- Total BUY: ₹{self._fmt_num(buy_total, 0)}")
        if sell_recs:
            lines.append(f"- Total SELL: ₹{self._fmt_num(sell_total, 0)}")
        if buy_recs and sell_recs:
            lines.append(f"- Net investment (BUY - SELL): ₹{self._fmt_num(net_total, 0)}")
        lines.append("")

        headers = ["Company", "Symbol", "Qty", "Price", "Value"]
        right = {2, 3, 4}

        def _rows(recs: List[Dict]) -> List[List[str]]:
            out_rows: List[List[str]] = []
            for r in recs:
                name = self._na(r.get("security_name") or r.get("symbol"))
                sym = self._na(r.get("symbol"))
                qty = self._fmt_num(r.get("quantity"), 0)
                try:
                    p = r.get("current_price")
                    price = "N/A" if p is None or float(p) <= 0 else self._fmt_num(p, 0)
                except Exception:
                    price = "N/A"
                val = self._fmt_num(r.get("amount"), 0)
                out_rows.append([name, sym, qty, price, val])
            return out_rows

        if buy_recs:
            lines.append("BUY")
            lines.append(self._ascii_table(headers, _rows(buy_recs), right_align_cols=right))
            lines.append(f"Total BUY: ₹{self._fmt_num(buy_total, 0)}")
            lines.append("")

        if sell_recs:
            lines.append("SELL")
            lines.append(self._ascii_table(headers, _rows(sell_recs), right_align_cols=right))
            lines.append(f"Total SELL: ₹{self._fmt_num(sell_total, 0)}")
            lines.append("")

        return "\n".join(lines).strip() + "\n"
    
    def generate_consolidated_email(self, session_id: int) -> Dict:
        """
        Generate consolidated email with ALL asset class recommendations
        Loads from RecommendationSession (database)
        
        Args:
            session_id: RecommendationSession ID
            
        Returns:
            Dict with recommendations, total_investment, asset_classes
        """
        # Load ALL recommendations from database (all asset classes)
        recommendations = Recommendation.query.filter_by(session_id=session_id).all()
        
        if not recommendations:
            return {
                'recommendations': [],
                'total_investment': 0.0,
                'asset_classes': [],
                'error': 'No recommendations found in session'
            }
        
        # Get client
        rec_session = RecommendationSession.query.get(session_id)
        if not rec_session:
            return {
                'recommendations': [],
                'total_investment': 0.0,
                'asset_classes': [],
                'error': 'Session not found'
            }
        
        client = Client.query.get(rec_session.client_id)
        if not client:
            return {
                'recommendations': [],
                'total_investment': 0.0,
                'asset_classes': [],
                'error': 'Client not found'
            }
        
        # Group by asset class
        by_asset_class = {}
        for rec in recommendations:
            asset_class = rec.asset_class.name if rec.asset_class else 'Equity'
            if asset_class not in by_asset_class:
                by_asset_class[asset_class] = []
            by_asset_class[asset_class].append(rec)
        
        # Format for email
        formatted_recs = []
        total_investment = 0.0
        
        for asset_class, recs in by_asset_class.items():
            # Add asset class header
            formatted_recs.append({
                'type': 'header',
                'asset_class': asset_class,
                'count': len(recs)
            })
            
            # Format each recommendation
            formatted, total = self.formatter.format_for_email(recs, client)
            formatted_recs.extend(formatted)
            total_investment += total
        
        return {
            'recommendations': formatted_recs,
            'total_investment': total_investment,
            'asset_classes': list(by_asset_class.keys())
        }
    
    def refresh_email_from_database(self, session_id: int) -> Dict:
        """
        Refresh email preview from database Section 1
        ALWAYS loads from RecommendationSession (single source of truth)
        
        Args:
            session_id: RecommendationSession ID
            
        Returns:
            Dict with recommendations, total_investment, synced_at
        """
        # Eager-load security (lazy load can yield None in some contexts; email would drop all rows)
        recommendations = (
            Recommendation.query.options(
                joinedload(Recommendation.security).joinedload(Security.asset_class),
                joinedload(Recommendation.asset_class),
            )
            .filter_by(session_id=session_id)
            .all()
        )
        
        if not recommendations:
            return {
                'recommendations': [],
                'total_investment': 0.0,
                'error': 'No recommendations found in session',
                'source': 'database'
            }
        
        # Get client
        rec_session = RecommendationSession.query.get(session_id)
        if not rec_session:
            return {
                'recommendations': [],
                'total_investment': 0.0,
                'error': 'Session not found',
                'source': 'database'
            }
        
        client = Client.query.get(rec_session.client_id)
        if not client:
            return {
                'recommendations': [],
                'total_investment': 0.0,
                'error': 'Client not found',
                'source': 'database'
            }
        
        # Format for email
        formatted, total = self.formatter.format_for_email(recommendations, client)
        
        return {
            'recommendations': formatted,
            'total_investment': total,
            'synced_at': datetime.now().isoformat(),
            'source': 'database',
            'session_id': session_id
        }
    
    def send_email_to_client(
        self,
        session_id: int,
        recipient_emails: List[str],
        subject: str,
        custom_message: str = '',
        sent_by_user_id: Optional[int] = None,
        send_format: str = "html",
        edited_email_html: Optional[str] = None,
        edited_email_text: Optional[str] = None,
        cc_emails: Optional[List[str]] = None,
    ) -> Dict:
        """
        Send email to client with recommendations
        
        Args:
            session_id: RecommendationSession ID
            recipient_emails: Email addresses to send to
            subject: Email subject
            custom_message: Custom message for email body
            
        Returns:
            Dict with success status
        """
        try:
            # Defensive: normalize recipients
            recipient_emails = [str(e).strip() for e in (recipient_emails or []) if str(e).strip()]
            recipient_email_log = ", ".join(recipient_emails)
            if not recipient_emails:
                return {'success': False, 'error': 'Recipient email address is required'}

            # Get email data
            email_data = self.refresh_email_from_database(session_id)
            
            if email_data.get('error'):
                return {
                    'success': False,
                    'error': email_data.get('error')
                }
            
            # Get client
            rec_session = RecommendationSession.query.get(session_id)
            client = Client.query.get(rec_session.client_id)
            
            # Calculate portfolio values
            # Import here to avoid circular import
            from unified_recommendation_service import UnifiedRecommendationService
            service = UnifiedRecommendationService()
            portfolio = service._get_or_create_portfolio(client)
            current_state = service._analyze_current_portfolio(client, portfolio)
            total_value = current_state.get('total_portfolio_value', 0.0)
            total_value_after_investment = total_value + email_data['total_investment']
            
            # Recalculate allocations
            for rec in email_data['recommendations']:
                if rec.get('type') == 'header':
                    continue
                
                if total_value > 0:
                    rec['current_allocation'] = (rec['current_value'] / total_value) * 100
                
                # Calculate future value
                # Amount is stored as positive, so subtract for SELL, add for BUY
                amount = float(rec.get('amount', 0))
                act = str(rec.get('action') or '').strip().upper()
                if act == 'BUY':
                    future_value = rec['current_value'] + amount
                elif act == 'SELL':
                    future_value = max(0, rec['current_value'] - amount)  # Subtract positive amount
                else:
                    future_value = rec['current_value']
                
                if total_value_after_investment > 0:
                    rec['target_allocation'] = (future_value / total_value_after_investment) * 100
                rec['target_value'] = future_value
            
            stock_recs, buy_recs, sell_recs = EmailService.partition_recommendations_for_email(
                email_data['recommendations']
            )
            buy_total = sum(float(r.get('amount', 0)) for r in buy_recs)
            sell_total = sum(float(r.get('amount', 0)) for r in sell_recs)
            
            # Generate email HTML
            current_month = datetime.now().strftime('%b %Y')
            
            email_html = render_template(
                'email/recommendations.html',
                client=client,
                recommendations=email_data['recommendations'],
                stock_recs=stock_recs,
                buy_recs=buy_recs,
                sell_recs=sell_recs,
                investment_amount=email_data['total_investment'],
                total_value=total_value,
                total_value_after_investment=total_value_after_investment,
                custom_message=custom_message,
                buy_total=buy_total,
                sell_total=sell_total,
                current_month=current_month
            )

            # Plain-text draft (used if sending as text or for logging)
            try:
                email_text = self.generate_plain_text_draft(
                    client=client,
                    recommendations=email_data["recommendations"],
                    custom_message=custom_message,
                    current_month=current_month,
                    total_value=total_value,
                    buy_total=buy_total,
                    sell_total=sell_total,
                )
            except Exception as text_err:
                self.logger.warning(f"Failed generating plain-text draft: {text_err}")
                email_text = ""

            # Get the sender email from config (must match MAIL_USERNAME for Gmail)
            from flask import current_app
            # For Gmail, sender must match the authenticated username
            sender_email = current_app.config.get('MAIL_USERNAME', 'anshul@equities4wealth.com').strip()
            if isinstance(sender_email, tuple):
                sender_email = sender_email[1] if len(sender_email) > 1 else sender_email[0]
            
            # Use charset='utf-8' so ₹ and other Unicode (emojis, non-Latin scripts) work in emails.
            # Without it, as_string() can raise 'ascii' codec can't encode character.
            _charset = "utf-8"
            fmt = (send_format or "html").strip().lower()
            if fmt in ("plain", "text", "plain_text"):
                body = (edited_email_text or email_text or custom_message or "").strip()
                msg = Message(subject=subject, recipients=recipient_emails, body=body, sender=sender_email, charset=_charset)
                if body:
                    email_html = f"<pre>{_html.escape(body)}</pre>"
                else:
                    email_html = None
            else:
                html_to_send = edited_email_html if (edited_email_html and edited_email_html.strip()) else email_html
                msg = Message(subject=subject, recipients=recipient_emails, body=(email_text or ""), html=(html_to_send or ""), sender=sender_email, charset=_charset)
                email_html = html_to_send
            
            from services.client_email_helpers import build_recommendation_cc_list, parse_email_list

            primary = (getattr(client, "email", None) or "").strip().lower()
            if cc_emails:
                msg.cc = [
                    e
                    for e in parse_email_list(", ".join(cc_emails))
                    if e.lower() != primary
                ]
            else:
                msg.cc = build_recommendation_cc_list(client)
            
            self.logger.debug(f"Email sender: {msg.sender}, recipients: {msg.recipients}, cc: {msg.cc}")

            # NOTE: Do not force Message-ID here. Flask-Mail / underlying mail libraries
            # already add one, and some receivers (Gmail) will reject if there are multiple.
            
            # Debug: Log what Flask-Mail is using
            from flask import current_app
            self.logger.info(f"Sending email to recipient(s): {recipient_email_log}")
            
            # Get Flask-Mail state from app extensions (correct way)
            mail_state = current_app.extensions.get('mail')
            if mail_state:
                smtp_auth_ok = bool(mail_state.password)
                self.logger.debug(
                    "Flask-Mail config: server=%s, username=%s, smtp_auth_configured=%s",
                    mail_state.server,
                    mail_state.username,
                    smtp_auth_ok,
                )
            else:
                self.logger.warning("Flask-Mail state not found in app.extensions")
            
            self.logger.debug(f"Message recipients: {msg.recipients}")
            self.logger.debug(f"Message sender: {msg.sender}")
            self.logger.debug(f"Message subject: {msg.subject}")
            
            # Send email using Flask-Mail
            self.logger.info("Sending email via Flask-Mail")
            
            mail_state = current_app.extensions.get('mail')
            if mail_state:
                smtp_auth_ok = bool(mail_state.password)
                self.logger.info(
                    "Flask-Mail config: server=%s, username=%s, smtp_auth_configured=%s",
                    mail_state.server,
                    mail_state.username,
                    smtp_auth_ok,
                )
            else:
                self.logger.warning("Flask-Mail state not found in app.extensions")
            
            self.logger.info(f"App config MAIL_SERVER: {current_app.config.get('MAIL_SERVER')}")
            self.logger.info(f"App config MAIL_USERNAME: {current_app.config.get('MAIL_USERNAME')}")
            self.logger.info(
                "App config smtp_auth_configured=%s",
                bool(current_app.config.get('MAIL_PASSWORD')),
            )
            self.logger.info(f"Message sender: {msg.sender}, recipients: {msg.recipients}")
            
            # Use direct SMTP (same as working test script) - avoids Flask-Mail 535 auth issues
            _env = __import__("os").environ
            _user = (_env.get("MAIL_USERNAME") or _env.get("SMTP_USERNAME") or "").strip()
            _raw = _env.get("MAIL_PASSWORD") or _env.get("SMTP_PASSWORD") or ""
            _pwd = str(_raw).strip().strip('"\'').replace(" ", "").replace("\r", "").replace("\n", "")
            mail_username = _user or current_app.config.get("MAIL_USERNAME", "").strip()
            mail_password = _pwd or current_app.config.get("MAIL_PASSWORD", "").strip()
            if not mail_username or not mail_password:
                raise ValueError(
                    "Email credentials not configured: "
                    f"MAIL_USERNAME={bool(mail_username)}, smtp_auth_configured={bool(mail_password)}"
                )
            self.logger.info(
                "Sending via direct SMTP: username=%s, smtp_auth_configured=%s",
                mail_username,
                bool(mail_password),
            )
            try:
                import smtplib
                smtp_server = _env.get("MAIL_SERVER") or _env.get("SMTP_SERVER") or "smtp.gmail.com"
                smtp_port = int(_env.get("MAIL_PORT") or _env.get("SMTP_PORT") or 587)
                use_ssl = (_env.get("MAIL_USE_SSL") or "false").lower() in ("true", "1", "on")
                use_tls = (_env.get("MAIL_USE_TLS") or "true").lower() in ("true", "1", "on")
                if use_ssl:
                    smtp = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=20)
                else:
                    smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
                    if use_tls:
                        smtp.starttls()
                smtp.login(mail_username, mail_password)
                # UTF-8: charset on Message + encode before send (supports ₹, emojis, etc.)
                msg_str = msg.as_string()
                msg_bytes = msg_str.encode("utf-8") if isinstance(msg_str, str) else msg_str
                smtp.sendmail(
                    mail_username,
                    list(sanitize_addresses(msg.send_to)),
                    msg_bytes,
                )
                smtp.quit()
                self.logger.info(f"Email sent successfully via direct SMTP to {recipient_email_log}")
            except Exception as send_error:
                _debug_log = "/home/inertia/app/logs/email_debug.log"
                _err_log = "/home/inertia/app/logs/email_send_errors.log"
                try:
                    import json, smtplib
                    _is_auth = isinstance(send_error, smtplib.SMTPAuthenticationError)
                    open(_debug_log, "a").write(json.dumps({"location": "email_service:send_error", "exc": str(send_error), "is_auth": _is_auth}, default=str) + "\n")
                    if _is_auth or "535" in str(send_error):
                        from datetime import datetime as _dt
                        open(_err_log, "a").write(
                            f"{_dt.utcnow().isoformat()}Z Direct SMTP 535: {send_error} — "
                            "Ensure SMTP auth secret is set in .env and env loaded before gunicorn.\n"
                        )
                except Exception:
                    pass
                self.logger.error(f"Direct SMTP send failed: {send_error}")
                self.logger.error(
                    "SMTP auth present in environment: %s",
                    bool(_env.get('MAIL_PASSWORD') or _env.get('SMTP_PASSWORD')),
                )
                raise
            
            # Update recommendation status to 'sent'
            recommendations = Recommendation.query.filter_by(session_id=session_id).all()
            for rec in recommendations:
                rec.status = 'sent'
                rec.sent_at = datetime.now()
            
            db.session.commit()
            
            # Log email
            from models import EmailLog
            effective_user_id: Optional[int] = None
            if sent_by_user_id:
                effective_user_id = int(sent_by_user_id)
            else:
                # Best-effort: if called from a request context, use the logged-in user.
                try:
                    from flask_login import current_user  # type: ignore
                    if getattr(current_user, "is_authenticated", False) and getattr(current_user, "id", None):
                        effective_user_id = int(current_user.id)
                except Exception:
                    effective_user_id = None

            if effective_user_id is None:
                # Don't fail the whole email send because logging metadata is missing.
                self.logger.warning(
                    "Email sent but skipping EmailLog insert because sent_by_user_id is not available "
                    f"(session_id={session_id}, recipient={recipient_email_log})"
                )
                email_log = None
            else:
                email_log = EmailLog(
                    client_id=client.id,
                    recipient_email=recipient_email_log,
                    subject=subject,
                    email_type='recommendations',
                    sent_by_user_id=effective_user_id,
                    email_html=email_html[:50000] if email_html and len(email_html) > 50000 else email_html,
                    custom_message=custom_message,
                    status='sent',
                    sent_at=datetime.utcnow(),
                )
                db.session.add(email_log)
                db.session.commit()

            # Advisory register (SEBI) — best-effort; never fail the send
            try:
                from services.db_cutover import advisory_register_enabled
                from services.advisory_register_service import append_from_session

                if advisory_register_enabled():
                    elog_id = getattr(email_log, "id", None) if email_log is not None else None
                    append_from_session(
                        int(session_id),
                        email_log_id=elog_id,
                        user_id=effective_user_id,
                        commit=True,
                    )
            except Exception as _ar_exc:
                self.logger.warning(
                    "Advisory register append skipped after send (session_id=%s): %s",
                    session_id,
                    _ar_exc,
                )
            
            return {
                'success': True,
                'message': 'Email sent successfully'
            }
            
        except Exception as e:
            # #region agent log
            _debug_log = "/home/inertia/app/logs/email_debug.log"
            _err_log = "/home/inertia/app/logs/email_send_errors.log"
            try:
                import json
                _p = {"hypothesisId": "H4", "location": "email_service.py:outer_except", "message": "outer exception returned to UI", "data": {"exc_type": type(e).__name__, "exc_msg": str(e)}, "timestamp": __import__("time").time() * 1000}
                open(_debug_log, "a").write(json.dumps(_p) + "\n")
                if "535" in str(e) or "Incorrect authentication" in str(e):
                    from datetime import datetime as _dt
                    open(_err_log, "a").write(
                        f"{_dt.utcnow().isoformat()}Z SMTP 535 (returned to UI): {str(e)} — "
                        "Fix: check MAIL_USERNAME and SMTP auth secret (e.g. Gmail app password).\n"
                    )
            except Exception:
                pass
            # #endregion
            # Log detailed error information for debugging
            from flask import current_app
            self.logger.error(f"Error sending email: {e}")
            self.logger.error(f"MAIL_SERVER: {current_app.config.get('MAIL_SERVER')}")
            self.logger.error(f"MAIL_USERNAME: {current_app.config.get('MAIL_USERNAME')}")
            self.logger.error(
                "smtp_auth_configured in app config: %s",
                bool(current_app.config.get('MAIL_PASSWORD')),
            )
            self.logger.error(f"MAIL_PORT: {current_app.config.get('MAIL_PORT')}")
            self.logger.error(f"MAIL_USE_TLS: {current_app.config.get('MAIL_USE_TLS')}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            db.session.rollback()
            err_msg = str(e)
            if '535' in err_msg or 'Incorrect authentication' in err_msg:
                err_msg = (
                    'Email login failed (SMTP 535): incorrect username or password. '
                    'On the server, set MAIL_USERNAME and a Gmail App Password in .env, '
                    'then restart the app. See docs/EMAIL_SETUP.md'
                )
            return {
                'success': False,
                'error': err_msg
            }

