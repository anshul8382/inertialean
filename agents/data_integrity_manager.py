"""
Data Integrity Manager Agent
============================
Validates data consistency across the system:
- Recommendation → Trade matching
- Cashflow reconciliation
- Corporate action verification
- Portfolio consistency
- Holdings vs Transactions reconciliation
- Duplicate detection
- Date integrity

This agent learns from user feedback to avoid flagging known patterns.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple, Set, Union
from sqlalchemy import func, and_, or_

from extensions import db
from models import (
    Client, Transaction, Recommendation, Cashflow, Holding,
    CorporateAction, PortfolioSnapshot, HoldingSnapshot, Security,
    DataIntegrityIssue, ClientBehaviorPattern, OpsTask,
)
from agents.base import BaseAgent
from services.recommendation_issue_hierarchy_service import (
    get_workflow_ids_with_cycle_level_non_execution,
    resolve_trade_level_issues_superseded_by_cycle,
    workflow_id_for_recommendation,
)
from services.recommendation_session_match_service import (
    CHECK_UNRECORDED_SUPERSEDED_SESSION,
    RecoSnap,
    TradeSnap,
    assign_unclaimed_trades_to_sent_sessions,
    best_trade_for_reco,
    claim_trades_for_executed,
    month_key_for_sent,
    pick_recorded_session_same_month,
    session_detail_payload,
    session_is_fully_unrecorded,
    superseded_session_message,
)
from services.task_assignment_service import (
    create_ops_task_from_issue,
    get_assignee_for_issue,
)

logger = logging.getLogger(__name__)


class DataIntegrityManager(BaseAgent):
    """
    Data Integrity Manager Agent
    
    Checks:
    - P0: Recommendation matching, Cashflow, Corporate actions, Portfolio consistency
    - P1: Holdings reconciliation, Price quality, Duplicates
    - P2: Date integrity, Negative values
    """
    
    @property
    def agent_name(self) -> str:
        return "data_integrity_manager"
    
    @property
    def agent_version(self) -> str:
        return "1.0.0"
    
    # =========================================================================
    # CONFIGURATION
    # =========================================================================
    
    # Default thresholds (can be overridden by client patterns)
    DEFAULT_QTY_TOLERANCE = 0.20  # ±20%
    DEFAULT_PRICE_TOLERANCE = 0.05  # ±5%
    DEFAULT_DATE_WINDOW_DAYS = 21  # 3 weeks
    DEFAULT_PORTFOLIO_TOLERANCE = 0.01  # 1% unexplained change
    
    # =========================================================================
    # MAIN RUN METHOD
    # =========================================================================
    
    def run_checks(self, client_id: int, since: datetime = None, **kwargs) -> List[DataIntegrityIssue]:
        """
        Run all integrity checks for a client.
        
        Args:
            client_id: Client to check
            since: Only check data after this date (for incremental runs)
        """
        issues = []
        
        # P0 Checks — hierarchy: cycle-level non-execution supersedes trade-level match issues
        blocked_workflows = get_workflow_ids_with_cycle_level_non_execution(client_id)
        resolve_trade_level_issues_superseded_by_cycle(client_id, blocked_workflows)
        issues.extend(
            self.check_recommendation_execution(
                client_id, since, blocked_workflow_ids=blocked_workflows
            )
        )
        issues.extend(self.check_cashflow_reconciliation(client_id, since))
        # SKIPPED: Corporate actions are handled by forward calc automatically
        # issues.extend(self.check_corporate_actions(client_id, since))
        # SKIPPED: Holdings are calculated on-the-fly via forward calc, not stored
        # Use portfolio construction for spot checks instead
        # issues.extend(self.check_holdings_vs_transactions(client_id))
        
        # P1 Checks
        issues.extend(self.check_duplicates(client_id, since))
        
        # P2 Checks
        issues.extend(self.check_date_integrity(client_id, since))
        issues.extend(self.check_negative_values(client_id))
        
        db.session.commit()
        return issues
    
    # =========================================================================
    # CHECK 1: RECOMMENDATION → TRADE MATCHING
    # =========================================================================
    
    def check_recommendation_execution(
        self,
        client_id: int,
        since: datetime = None,
        blocked_workflow_ids: Optional[Set[int]] = None,
    ) -> List[DataIntegrityIssue]:
        """
        Check that sent recommendations are recorded as trades.

        Matching is session-first: executed recos claim fills, remaining trades bind
        to at most one sent session (qty-weighted). G6 only inside that session.
        A fully unrecorded session in a month where another session was recorded
        becomes one superseded-session confirm issue (not per-security G5).
        """
        issues: List[DataIntegrityIssue] = []
        blocked = blocked_workflow_ids or set()
        date_window_days = self._get_date_window(client_id)

        query = Recommendation.query.filter(
            Recommendation.client_id == client_id,
            Recommendation.sent_at.isnot(None),
            Recommendation.status.in_(["sent", "executed"]),
        )
        if since:
            query = query.filter(
                or_(
                    Recommendation.sent_at >= since,
                    Recommendation.executed_at >= since,
                )
            )
        recommendations = query.all()
        rec_by_id = {r.id: r for r in recommendations}

        sent_open: List[RecoSnap] = []
        executed: List[RecoSnap] = []
        for rec in recommendations:
            snap = self._reco_to_snap(rec)
            if not snap.is_executable:
                continue
            wf_id = workflow_id_for_recommendation(rec)
            if snap.status == "sent" and not snap.executed_at:
                if wf_id is not None and wf_id in blocked:
                    continue
                sent_open.append(snap)
            elif snap.status == "executed" or snap.executed_at:
                executed.append(snap)

        pending_unexecuted: Dict[Tuple[str, Union[int, str]], List[Recommendation]] = defaultdict(list)
        current_mismatch_pairs: Set[Tuple[int, int]] = set()
        current_superseded: Dict[int, Tuple[int, List[RecoSnap]]] = {}

        dates = [s.sent_at for s in sent_open + executed if s.sent_at]
        trades_orm: List[Transaction] = []
        if dates:
            t_start = min(dates)
            t_end = max(dates) + timedelta(days=date_window_days)
            trades_orm = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.transaction_date >= t_start,
                Transaction.transaction_date <= t_end,
                Transaction.type.in_(["BUY", "Buy", "buy", "SELL", "Sell", "sell"]),
            ).all()

        trade_snaps = [self._txn_to_snap(t) for t in trades_orm]
        trades_by_id = {t.id: t for t in trade_snaps}
        txn_by_id = {t.id: t for t in trades_orm}

        claimed = claim_trades_for_executed(executed, trade_snaps, date_window_days)
        assigned = assign_unclaimed_trades_to_sent_sessions(
            sent_open, trade_snaps, date_window_days, set(claimed)
        )
        owned_trade_ids = set(claimed) | set(assigned)

        by_session: Dict[int, List[RecoSnap]] = defaultdict(list)
        no_session: List[RecoSnap] = []
        for snap in sent_open:
            if snap.session_id:
                by_session[int(snap.session_id)].append(snap)
            else:
                no_session.append(snap)

        now = datetime.utcnow()

        for sid, recs_s in by_session.items():
            sess_trade_ids = {tid for tid, ss in assigned.items() if ss == sid}
            sess_trades = [trades_by_id[tid] for tid in sess_trade_ids if tid in trades_by_id]
            fully_unrec = session_is_fully_unrecorded(recs_s, sess_trade_ids)
            mk = month_key_for_sent(recs_s)
            peer = (
                pick_recorded_session_same_month(sid, mk, executed, claimed)
                if fully_unrec and mk
                else None
            )
            if fully_unrec and peer:
                current_superseded[sid] = (peer, recs_s)
                continue

            used_trades: Set[int] = set()
            for snap in recs_s:
                rec = rec_by_id[snap.id]
                match = best_trade_for_reco(snap, sess_trades, date_window_days, used_trades)
                if match:
                    used_trades.add(match.id)
                    txn = txn_by_id.get(match.id)
                    if not txn:
                        continue
                    qty = self._qty_mismatch_payload(client_id, rec, txn)
                    price = self._price_mismatch_payload(client_id, rec, txn)
                    self._resolve_split_qty_price_issues_for_trade(client_id, rec.id, txn.id)
                    if qty or price:
                        comb = self._create_trade_execution_mismatch_from_payloads(
                            client_id, rec, txn, qty, price
                        )
                        if comb:
                            issues.append(comb)
                            current_mismatch_pairs.add((rec.id, txn.id))
                else:
                    if not rec.sent_at:
                        continue
                    window_end = rec.sent_at + timedelta(days=date_window_days)
                    if now <= window_end:
                        continue
                    should_skip, _ = self.should_skip_issue(
                        client_id,
                        "RECOMMENDATION_MATCH",
                        "unexecuted_recommendation",
                        recommendation_id=rec.id,
                        security_id=rec.security_id,
                        reference_date=str(rec.sent_at.date()) if rec.sent_at else None,
                    )
                    if should_skip:
                        continue
                    pending_unexecuted[self._unexecuted_batch_group_key(rec)].append(rec)

        unowned_txns = [t for t in trades_orm if t.id not in owned_trade_ids]
        for snap in no_session:
            rec = rec_by_id[snap.id]
            window_end = rec.sent_at + timedelta(days=date_window_days) if rec.sent_at else now
            matching_txns = [
                t
                for t in unowned_txns
                if t.security_id == rec.security_id
                and (t.type or "").upper() == self._action_to_type(rec.action)
                and rec.sent_at
                and rec.sent_at <= t.transaction_date <= window_end
            ]
            if not matching_txns:
                if now <= window_end:
                    continue
                should_skip, _ = self.should_skip_issue(
                    client_id,
                    "RECOMMENDATION_MATCH",
                    "unexecuted_recommendation",
                    recommendation_id=rec.id,
                    security_id=rec.security_id,
                    reference_date=str(rec.sent_at.date()) if rec.sent_at else None,
                )
                if should_skip:
                    continue
                pending_unexecuted[self._unexecuted_batch_group_key(rec)].append(rec)
            else:
                best_match = self._find_best_match(rec, matching_txns)
                if best_match:
                    qty = self._qty_mismatch_payload(client_id, rec, best_match)
                    price = self._price_mismatch_payload(client_id, rec, best_match)
                    self._resolve_split_qty_price_issues_for_trade(
                        client_id, rec.id, best_match.id
                    )
                    if qty or price:
                        comb = self._create_trade_execution_mismatch_from_payloads(
                            client_id, rec, best_match, qty, price
                        )
                        if comb:
                            issues.append(comb)
                            current_mismatch_pairs.add((rec.id, best_match.id))

        self._reconcile_stale_mismatches(client_id, current_mismatch_pairs)
        self._reconcile_stale_unexecuted_batches(client_id, pending_unexecuted)
        self._reconcile_stale_superseded_sessions(client_id, set(current_superseded))

        for sid, (peer_id, _recs_s) in current_superseded.items():
            issue = self._upsert_unrecorded_superseded_session(
                client_id, sid, peer_id
            )
            if issue:
                issues.append(issue)

        for key, recs in pending_unexecuted.items():
            batch_issue = self._upsert_unexecuted_recommendations_batch(
                client_id, key, recs, date_window_days
            )
            if batch_issue:
                issues.append(batch_issue)

        return issues

    def _reco_to_snap(self, rec: Recommendation) -> RecoSnap:
        session_name = None
        if rec.session_id and rec.session:
            session_name = rec.session.session_name
        return RecoSnap(
            id=rec.id,
            session_id=rec.session_id,
            security_id=rec.security_id,
            symbol=rec.security.symbol if rec.security else "Unknown",
            action=(rec.action or "").strip().lower(),
            quantity=float(rec.quantity) if rec.quantity is not None else None,
            target_price=float(rec.target_price) if rec.target_price is not None else None,
            actual_price=float(rec.actual_price) if rec.actual_price is not None else None,
            status=(rec.status or "").strip().lower(),
            sent_at=rec.sent_at,
            executed_at=rec.executed_at,
            is_hold=bool(rec.is_hold_line),
            session_name=session_name,
        )

    @staticmethod
    def _txn_to_snap(txn: Transaction) -> TradeSnap:
        return TradeSnap(
            id=txn.id,
            security_id=txn.security_id,
            txn_type=(txn.type or "").strip().upper(),
            quantity=float(txn.quantity or 0),
            price=float(txn.price or 0),
            transaction_date=txn.transaction_date,
        )

    def _session_snaps_for_payload(self, client_id: int, session_id: int) -> List[RecoSnap]:
        recs = Recommendation.query.filter_by(
            client_id=client_id, session_id=session_id
        ).all()
        return [self._reco_to_snap(r) for r in recs]

    def _reconcile_stale_mismatches(
        self, client_id: int, current_pairs: Set[Tuple[int, int]]
    ) -> None:
        open_mm = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
            DataIntegrityIssue.check_name == "trade_execution_mismatch",
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        ).all()
        for iss in open_mm:
            pair = (iss.recommendation_id, iss.transaction_id)
            if pair[0] is None or pair[1] is None or pair not in current_pairs:
                self._resolve_batch_issue_and_close_tasks(iss)
                iss.resolution_notes = (
                    "Trade no longer assigned to this recommendation session "
                    "(or quantity/price now within tolerance)."
                )

    def _find_open_superseded_session_issue(
        self, client_id: int, unused_session_id: int
    ) -> Optional[DataIntegrityIssue]:
        q = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
            DataIntegrityIssue.check_name == CHECK_UNRECORDED_SUPERSEDED_SESSION,
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        )
        for iss in q.all():
            uid = (iss.details or {}).get("unused_session", {}).get("session_id")
            if uid is None:
                uid = (iss.details or {}).get("unused_session_id")
            try:
                if int(uid) == int(unused_session_id):
                    return iss
            except (TypeError, ValueError):
                continue
        return None

    def _user_resolved_same_superseded_session(
        self, client_id: int, unused_session_id: int, recorded_session_id: int
    ) -> bool:
        gkey = f"unrecorded_session_{unused_session_id}"
        prev = (
            DataIntegrityIssue.query.filter(
                DataIntegrityIssue.client_id == client_id,
                DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
                DataIntegrityIssue.check_name == CHECK_UNRECORDED_SUPERSEDED_SESSION,
                DataIntegrityIssue.status == "resolved",
                DataIntegrityIssue.group_key == gkey,
                DataIntegrityIssue.resolved_by.isnot(None),
            )
            .order_by(DataIntegrityIssue.resolved_at.desc())
            .limit(10)
            .all()
        )
        for iss in prev:
            rec_id = (iss.details or {}).get("recorded_session", {}).get("session_id")
            try:
                if rec_id is not None and int(rec_id) == int(recorded_session_id):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _reconcile_stale_superseded_sessions(
        self, client_id: int, current_unused_ids: Set[int]
    ) -> None:
        open_iss = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
            DataIntegrityIssue.check_name == CHECK_UNRECORDED_SUPERSEDED_SESSION,
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        ).all()
        for iss in open_iss:
            uid = (iss.details or {}).get("unused_session", {}).get("session_id")
            try:
                uid_int = int(uid)
            except (TypeError, ValueError):
                self._resolve_batch_issue_and_close_tasks(iss)
                continue
            if uid_int not in current_unused_ids:
                self._resolve_batch_issue_and_close_tasks(iss)
                iss.resolution_notes = (
                    "Unused session is no longer fully unrecorded, or the later "
                    "recorded session in the same month no longer applies."
                )

    def _upsert_unrecorded_superseded_session(
        self, client_id: int, unused_session_id: int, recorded_session_id: int
    ) -> Optional[DataIntegrityIssue]:
        unused_snaps = self._session_snaps_for_payload(client_id, unused_session_id)
        recorded_snaps = self._session_snaps_for_payload(client_id, recorded_session_id)
        unused_detail = session_detail_payload(unused_session_id, unused_snaps)
        recorded_detail = session_detail_payload(recorded_session_id, recorded_snaps)
        mk = month_key_for_sent(unused_snaps) or datetime.utcnow().strftime("%Y-%m")
        message = superseded_session_message(
            unused_detail.get("sent_at"),
            mk,
            recorded_detail.get("sent_at"),
            unused_session_id,
            recorded_session_id,
        )
        suggested = (
            "Compare both recommendation lists. If session "
            f"{unused_session_id} was replaced by session {recorded_session_id}, "
            "confirm unused advice and close this alert (one close for the whole session)."
        )
        details: Dict[str, Any] = {
            "month_key": mk,
            "unused_session_id": unused_session_id,
            "recorded_session_id": recorded_session_id,
            "unused_session": unused_detail,
            "recorded_session": recorded_detail,
            "primary_problem": message,
            "close_note": "Close once to confirm unused advice; do not close security-by-security.",
        }
        ref_dates = [r.sent_at.date() for r in unused_snaps if r.sent_at]
        ref_date = min(ref_dates) if ref_dates else datetime.utcnow().date()
        gkey = f"unrecorded_session_{unused_session_id}"
        existing = self._find_open_superseded_session_issue(client_id, unused_session_id)
        if existing:
            existing.message = message
            merged = dict(existing.details or {})
            merged.update(details)
            existing.details = merged
            existing.severity = "info"
            existing.suggested_action = suggested
            existing.reference_date = ref_date
            if not existing.assigned_to:
                aid = get_assignee_for_issue(existing)
                if aid:
                    existing.assigned_to = aid
                    db.session.flush()
            create_ops_task_from_issue(existing, assignee_id=existing.assigned_to)
            return existing
        if self._user_resolved_same_superseded_session(
            client_id, unused_session_id, recorded_session_id
        ):
            logger.info(
                "Skipping unrecorded_superseded_session recreate: user-resolved "
                "unused_session=%s recorded_session=%s",
                unused_session_id,
                recorded_session_id,
            )
            return None
        return self.create_issue(
            client_id=client_id,
            check_category="RECOMMENDATION_MATCH",
            check_name=CHECK_UNRECORDED_SUPERSEDED_SESSION,
            severity="info",
            message=message,
            details=details,
            suggested_action=suggested,
            recommendation_id=None,
            security_id=None,
            reference_date=ref_date,
            group_key=gkey,
        )

    @staticmethod
    def _unexecuted_batch_group_key(rec: Recommendation) -> Tuple[str, Union[int, str]]:
        wf_id = workflow_id_for_recommendation(rec)
        if wf_id is not None:
            return ("wf", wf_id)
        if rec.session_id:
            return ("sess", rec.session_id)
        ym = rec.sent_at.strftime("%Y-%m") if rec.sent_at else "unknown"
        return ("month", ym)

    def _batch_issue_group_key_from_details(self, d: Dict[str, Any]) -> Optional[Tuple[str, Union[int, str]]]:
        if not d:
            return None
        gk = d.get("group_kind")
        if gk == "workflow" and d.get("workflow_id") is not None:
            try:
                return ("wf", int(d["workflow_id"]))
            except (TypeError, ValueError):
                return None
        if gk == "session" and d.get("session_id") is not None:
            try:
                return ("sess", int(d["session_id"]))
            except (TypeError, ValueError):
                return None
        if gk == "month" and d.get("month_key"):
            return ("month", str(d["month_key"]))
        if d.get("workflow_id") is not None:
            try:
                return ("wf", int(d["workflow_id"]))
            except (TypeError, ValueError):
                pass
        return None

    def _find_open_unexecuted_batch_issue(
        self, client_id: int, key: Tuple[str, Union[int, str]]
    ) -> Optional[DataIntegrityIssue]:
        kind, kid = key
        q = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
            DataIntegrityIssue.check_name == "unexecuted_recommendations_batch",
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        )
        for iss in q.all():
            parsed = self._batch_issue_group_key_from_details(iss.details or {})
            if parsed == (kind, kid):
                return iss
        return None

    @staticmethod
    def _rec_ids_from_batch_issue_details(details: Dict[str, Any]) -> List[int]:
        """Recommendation IDs stored on a batch issue's related_items checklist."""
        ids: List[int] = []
        for item in (details or {}).get("related_items") or []:
            if not isinstance(item, dict):
                continue
            rid = item.get("recommendation_id")
            if rid is None:
                continue
            try:
                ids.append(int(rid))
            except (TypeError, ValueError):
                continue
        return ids

    def _user_resolved_same_unexecuted_batch(
        self,
        client_id: int,
        group_key: str,
        current_rec_ids_sorted: List[int],
    ) -> bool:
        """
        True if a user-resolved batch issue exists for this group_key with the same
        set of recommendation_ids as the current pending batch. Stops the audit from
        recreating the same issue after the user closed it while data is unchanged.
        """
        if not current_rec_ids_sorted:
            return False
        target = tuple(current_rec_ids_sorted)
        prev_issues = (
            DataIntegrityIssue.query.filter(
                DataIntegrityIssue.client_id == client_id,
                DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
                DataIntegrityIssue.check_name == "unexecuted_recommendations_batch",
                DataIntegrityIssue.status == "resolved",
                DataIntegrityIssue.group_key == group_key,
                DataIntegrityIssue.resolved_by.isnot(None),
            )
            .order_by(DataIntegrityIssue.resolved_at.desc())
            .limit(25)
            .all()
        )
        for prev in prev_issues:
            prev_ids = self._rec_ids_from_batch_issue_details(prev.details or {})
            if not prev_ids:
                continue
            if tuple(sorted(prev_ids)) == target:
                return True
        return False

    def _resolve_batch_issue_and_close_tasks(self, issue: DataIntegrityIssue) -> None:
        now = datetime.utcnow()
        open_statuses = ["pending", "in_progress", "snoozed"]
        det = dict(issue.details or {})
        det["resolved_reason"] = "no_longer_pending_unexecuted_lines"
        issue.details = det
        issue.status = "resolved"
        issue.resolution_type = "auto"
        issue.resolved_at = now
        issue.resolution_notes = "All listed recommendations now have a matching trade or left the batch scope."
        for task in OpsTask.query.filter(
            OpsTask.data_integrity_issue_id == issue.id,
            OpsTask.status.in_(open_statuses),
        ).all():
            task.status = "completed"
            task.completed_at = now
            task.completed_by = None
            task.reminder_at = None
            task.reminder_sent = True
            task.updated_at = now

    def _reconcile_stale_unexecuted_batches(
        self,
        client_id: int,
        pending_by_key: Dict[Tuple[str, Union[int, str]], List[Recommendation]],
    ) -> None:
        open_batches = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
            DataIntegrityIssue.check_name == "unexecuted_recommendations_batch",
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        ).all()
        for iss in open_batches:
            key = self._batch_issue_group_key_from_details(iss.details or {})
            if key is None:
                self._resolve_batch_issue_and_close_tasks(iss)
                continue
            recs = pending_by_key.get(key, [])
            if not recs:
                self._resolve_batch_issue_and_close_tasks(iss)

    def _resolve_line_unexecuted_superseded_by_batch(
        self, client_id: int, recommendation_ids: List[int]
    ) -> None:
        if not recommendation_ids:
            return
        now = datetime.utcnow()
        open_statuses = ["pending", "in_progress", "snoozed"]
        for rid in recommendation_ids:
            for iss in DataIntegrityIssue.query.filter(
                DataIntegrityIssue.client_id == client_id,
                DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
                DataIntegrityIssue.check_name == "unexecuted_recommendation",
                DataIntegrityIssue.recommendation_id == rid,
                DataIntegrityIssue.status.in_(["open", "baseline"]),
            ).all():
                det = dict(iss.details or {})
                det["superseded_by"] = "unexecuted_recommendations_batch"
                iss.details = det
                iss.status = "resolved"
                iss.resolution_type = "superseded"
                iss.resolved_at = now
                iss.resolution_notes = (
                    "Consolidated into a single batch issue with full related_items checklist."
                )
                for task in OpsTask.query.filter(
                    OpsTask.data_integrity_issue_id == iss.id,
                    OpsTask.status.in_(open_statuses),
                ).all():
                    task.status = "completed"
                    task.completed_at = now
                    task.completed_by = None
                    task.reminder_at = None
                    task.reminder_sent = True
                    task.updated_at = now

    def _upsert_unexecuted_recommendations_batch(
        self,
        client_id: int,
        key: Tuple[str, Union[int, str]],
        recs: List[Recommendation],
        window_days: int,
    ) -> Optional[DataIntegrityIssue]:
        if not recs:
            return None

        kind, kid = key
        recs_sorted = sorted(recs, key=lambda r: r.sent_at or datetime.min)
        related_items: List[Dict[str, Any]] = []
        symbols: List[str] = []
        for r in recs_sorted:
            sym = r.security.symbol if r.security else "Unknown"
            symbols.append(sym)
            related_items.append(
                {
                    "recommendation_id": r.id,
                    "security_id": r.security_id,
                    "security_symbol": sym,
                    "action": r.action,
                    "quantity": float(r.quantity) if r.quantity is not None else None,
                    "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                    "problem": "no_matching_trade_in_window",
                }
            )

        ref_date = min((r.sent_at.date() for r in recs_sorted if r.sent_at), default=datetime.utcnow().date())

        if kind == "wf":
            scope = {
                "group_kind": "workflow",
                "workflow_id": int(kid),
                "session_id": None,
                "month_key": None,
            }
        elif kind == "sess":
            scope = {
                "group_kind": "session",
                "workflow_id": None,
                "session_id": int(kid),
                "month_key": None,
            }
        else:
            scope = {
                "group_kind": "month",
                "workflow_id": None,
                "session_id": None,
                "month_key": str(kid),
            }

        details: Dict[str, Any] = {
            **scope,
            "window_days": window_days,
            "count": len(recs_sorted),
            "related_items": related_items,
            "primary_problem": (
                f"{len(recs_sorted)} sent recommendation(s) have no matching trade within "
                f"{window_days} days."
            ),
            "checklist_note": (
                "Use related_items as the checklist: record trades, mark recommendations executed, "
                "or cancel lines; nothing should be missed."
            ),
        }

        syms = ", ".join(symbols[:8])
        if len(symbols) > 8:
            syms += f", +{len(symbols) - 8} more"
        message = (
            f"Batch: {len(recs_sorted)} recommendation(s) not executed within {window_days}d — {syms}"
        )
        suggested = (
            "Fix the underlying execution gap using related_items (checklist). "
            "One task tracks the whole batch."
        )

        rec_ids = [r.id for r in recs_sorted]
        self._resolve_line_unexecuted_superseded_by_batch(client_id, rec_ids)

        gkey = f"unexec_batch_{kind}_{kid}"
        existing = self._find_open_unexecuted_batch_issue(client_id, key)

        if existing:
            existing.message = message
            merged = dict(existing.details or {})
            merged.update(details)
            existing.details = merged
            existing.severity = "critical" if len(recs_sorted) >= 5 else "warning"
            existing.suggested_action = suggested
            existing.reference_date = ref_date
            if not existing.assigned_to:
                aid = get_assignee_for_issue(existing)
                if aid:
                    existing.assigned_to = aid
                    db.session.flush()
            create_ops_task_from_issue(existing, assignee_id=existing.assigned_to)
            return existing

        rec_ids_sorted = sorted(rec_ids)
        if self._user_resolved_same_unexecuted_batch(client_id, gkey, rec_ids_sorted):
            logger.info(
                "Skipping unexecuted_recommendations_batch recreate: user-resolved issue "
                "with same group_key=%s and recommendation_ids=%s",
                gkey,
                rec_ids_sorted,
            )
            return None

        return self.create_issue(
            client_id=client_id,
            check_category="RECOMMENDATION_MATCH",
            check_name="unexecuted_recommendations_batch",
            severity="critical" if len(recs_sorted) >= 5 else "warning",
            message=message,
            details=details,
            suggested_action=suggested,
            recommendation_id=None,
            security_id=None,
            reference_date=ref_date,
            group_key=gkey,
        )
    
    def _get_date_window(self, client_id: int) -> int:
        """Get date window for matching, considering client patterns."""
        pattern = self.get_client_pattern(client_id, 'RECOMMENDATION_MATCH', 'execution_delay')
        if pattern:
            return pattern.pattern_value.get('typical_delay_days', self.DEFAULT_DATE_WINDOW_DAYS)
        return self.DEFAULT_DATE_WINDOW_DAYS
    
    def _action_to_type(self, action: str) -> str:
        """Convert recommendation action to transaction type."""
        action_map = {'buy': 'BUY', 'sell': 'SELL'}
        return action_map.get(action.lower(), action.upper())
    
    def _find_best_match(self, rec: Recommendation, txns: List[Transaction]) -> Optional[Transaction]:
        """Find the best matching transaction for a recommendation."""
        if not txns:
            return None
        
        # Simple: return closest by date
        return min(txns, key=lambda t: abs((t.transaction_date - rec.sent_at).total_seconds()))

    def _resolve_split_qty_price_issues_for_trade(
        self, client_id: int, recommendation_id: int, transaction_id: int
    ) -> None:
        """Close legacy quantity_mismatch + price_mismatch rows for the same rec+trade."""
        now = datetime.utcnow()
        open_statuses = ["pending", "in_progress", "snoozed"]
        legacy = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
            DataIntegrityIssue.check_name.in_(["quantity_mismatch", "price_mismatch"]),
            DataIntegrityIssue.recommendation_id == recommendation_id,
            DataIntegrityIssue.transaction_id == transaction_id,
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        ).all()
        for issue in legacy:
            det = dict(issue.details or {})
            det["superseded_by"] = "trade_execution_mismatch"
            issue.details = det
            issue.status = "resolved"
            issue.resolution_type = "superseded"
            issue.resolved_at = now
            issue.resolution_notes = (
                "Superseded: quantity and price merged into one trade_execution_mismatch issue."
            )
            for task in OpsTask.query.filter(
                OpsTask.data_integrity_issue_id == issue.id,
                OpsTask.status.in_(open_statuses),
            ).all():
                task.status = "completed"
                task.completed_at = now
                task.completed_by = None
                task.reminder_at = None
                task.reminder_sent = True
                task.updated_at = now

    def _qty_mismatch_payload(
        self, client_id: int, rec: Recommendation, txn: Transaction
    ) -> Optional[Dict[str, Any]]:
        if not rec.quantity:
            return None
        rec_qty = float(rec.quantity)
        txn_qty = float(txn.quantity)
        tolerance = self.DEFAULT_QTY_TOLERANCE
        multiplier = 1.0
        pattern = self.get_client_pattern(client_id, "RECOMMENDATION_MATCH", "quantity_multiplier")
        if pattern:
            multiplier = pattern.pattern_value.get("multiplier", 1.0)
            tolerance = pattern.pattern_value.get("tolerance", self.DEFAULT_QTY_TOLERANCE)
        expected_qty = rec_qty * multiplier
        variance = abs(txn_qty - expected_qty) / expected_qty if expected_qty > 0 else 0
        if variance <= tolerance:
            return None
        return {
            "summary": f"recommended {rec_qty:.0f}, executed {txn_qty:.0f} ({variance * 100:.1f}% difference)",
            "details": {
                "expected_qty": rec_qty,
                "expected_with_multiplier": expected_qty,
                "actual_qty": txn_qty,
                "variance_pct": variance * 100,
                "tolerance_pct": tolerance * 100,
                "multiplier_applied": multiplier,
            },
        }

    def _price_mismatch_payload(
        self, client_id: int, rec: Recommendation, txn: Transaction
    ) -> Optional[Dict[str, Any]]:
        if not rec.target_price:
            return None
        rec_price = float(rec.target_price)
        txn_price = float(txn.price)
        tolerance = self.DEFAULT_PRICE_TOLERANCE
        pattern = self.get_client_pattern(client_id, "RECOMMENDATION_MATCH", "price_tolerance")
        if pattern:
            tolerance = pattern.pattern_value.get(
                "tolerance_pct", self.DEFAULT_PRICE_TOLERANCE * 100
            ) / 100
        variance = abs(txn_price - rec_price) / rec_price if rec_price > 0 else 0
        if variance <= tolerance:
            return None
        return {
            "summary": f"target ₹{rec_price:.2f}, executed ₹{txn_price:.2f} ({variance * 100:.1f}% difference)",
            "details": {
                "target_price": rec_price,
                "actual_price": txn_price,
                "variance_pct": variance * 100,
                "tolerance_pct": tolerance * 100,
            },
        }

    def _create_trade_execution_mismatch_from_payloads(
        self,
        client_id: int,
        rec: Recommendation,
        txn: Transaction,
        qty: Optional[Dict[str, Any]],
        price: Optional[Dict[str, Any]],
    ) -> Optional[DataIntegrityIssue]:
        """
        Single issue when quantity and/or price diverge from recommendation for the matched trade.
        Caller must resolve legacy split issues and only call when qty or price payload is set.
        """
        if not qty and not price:
            return None

        sym = rec.security.symbol if rec.security else "Unknown"
        parts = []
        if qty:
            parts.append(f"quantity {qty['summary']}")
        if price:
            parts.append(f"price {price['summary']}")
        message = f"{sym}: " + "; ".join(parts)

        details: Dict[str, Any] = {
            "recommendation_id": rec.id,
            "transaction_id": txn.id,
            "security_id": rec.security_id,
            "security_symbol": sym,
        }
        if qty:
            details["quantity"] = qty["details"]
        if price:
            details["price"] = price["details"]

        severity = "warning" if qty else "info"
        return self.create_issue(
            client_id=client_id,
            check_category="RECOMMENDATION_MATCH",
            check_name="trade_execution_mismatch",
            severity=severity,
            message=message,
            details=details,
            suggested_action="Verify executed quantity and price against the recommendation",
            recommendation_id=rec.id,
            transaction_id=txn.id,
            security_id=rec.security_id,
            reference_date=txn.transaction_date.date(),
        )
    
    # =========================================================================
    # CHECK 2: CASHFLOW RECONCILIATION
    # =========================================================================
    
    def check_cashflow_reconciliation(self, client_id: int, since: datetime = None) -> List[DataIntegrityIssue]:
        """
        Check cashflow consistency with transactions.
        
        Checks:
        - Net investment mismatch (cashflows vs trades)
        - Orphan cashflows (inflow with no trades)
        """
        issues = []
        
        # Some legacy clients have dummy transaction dates (e.g., 2000-01-01) while
        # cashflow dates are correct. In such cases, month-based cashflow vs trade
        # reconciliation will produce persistent false positives.
        #
        # Client override:
        # ClientBehaviorPattern(check_category='CASHFLOW', pattern_type='ignore_dummy_trade_dates')
        # pattern_value: {"dummy_dates": ["2000-01-01"], "mode": "suppress_monthly_recon"}
        dummy_dates = None
        dummy_mode = None
        dummy_pattern = self.get_client_pattern(client_id, 'CASHFLOW', 'ignore_dummy_trade_dates')
        if dummy_pattern and isinstance(dummy_pattern.pattern_value, dict):
            dummy_dates = set(str(d) for d in (dummy_pattern.pattern_value.get('dummy_dates') or []))
            dummy_mode = dummy_pattern.pattern_value.get('mode') or 'suppress_monthly_recon'
        
        # Get cashflows
        cf_query = Cashflow.query.filter_by(client_id=client_id)
        if since:
            cf_query = cf_query.filter(Cashflow.date >= since)
        
        cashflows = cf_query.all()
        
        # Group by month for reconciliation
        monthly_cashflows = {}
        for cf in cashflows:
            month_key = cf.date.strftime('%Y-%m')
            if month_key not in monthly_cashflows:
                monthly_cashflows[month_key] = {'inflow': Decimal(0), 'outflow': Decimal(0)}
            if cf.type == 'INFLOW':
                monthly_cashflows[month_key]['inflow'] += cf.amount
            else:
                monthly_cashflows[month_key]['outflow'] += cf.amount
        
        # Get transactions by month
        txn_query = Transaction.query.filter_by(client_id=client_id)
        if since:
            txn_query = txn_query.filter(Transaction.transaction_date >= since)
        
        transactions = txn_query.all()
        
        # Detect presence of dummy-dated trades (may be filtered out by incremental runs)
        has_dummy_trades = False
        if dummy_dates:
            for txn in transactions:
                try:
                    if txn.transaction_date and txn.transaction_date.date().isoformat() in dummy_dates:
                        has_dummy_trades = True
                        break
                except Exception:
                    continue
            
            if since and not has_dummy_trades:
                # Check full history for dummy dates (do not restrict to `since`)
                try:
                    any_dummy = Transaction.query.filter_by(client_id=client_id).filter(
                        func.date(Transaction.transaction_date).in_(list(dummy_dates))
                    ).first()
                    has_dummy_trades = bool(any_dummy)
                except Exception:
                    # If this fails, keep best-effort result
                    pass
        
        monthly_trades = {}
        for txn in transactions:
            month_key = txn.transaction_date.strftime('%Y-%m')
            if month_key not in monthly_trades:
                monthly_trades[month_key] = {'buy': Decimal(0), 'sell': Decimal(0)}
            if txn.type.upper() == 'BUY':
                monthly_trades[month_key]['buy'] += txn.amount
            else:
                monthly_trades[month_key]['sell'] += txn.amount
        
        # Check each month
        all_months = set(monthly_cashflows.keys()) | set(monthly_trades.keys())
        
        for month in all_months:
            cf = monthly_cashflows.get(month, {'inflow': Decimal(0), 'outflow': Decimal(0)})
            tr = monthly_trades.get(month, {'buy': Decimal(0), 'sell': Decimal(0)})
            
            # Net from cashflows: inflow - outflow (money coming in)
            cf_net = cf['inflow'] - cf['outflow']
            # Net from trades: buy - sell (money going out for buys)
            tr_net = tr['buy'] - tr['sell']
            
            # They should roughly match (inflow funds buys)
            if float(cf['inflow']) > 0 and float(tr['buy']) == 0:
                # If dummy trade dates are present, month-wise reconciliation is unreliable.
                # Suppress this false positive to avoid noise.
                if dummy_dates and has_dummy_trades and dummy_mode == 'suppress_monthly_recon':
                    continue
                # Inflow but no trades - potential issue
                issue = self.create_issue(
                    client_id=client_id,
                    check_category='CASHFLOW',
                    check_name='orphan_cashflow',
                    severity='warning',
                    message=f"Cashflow inflow of ₹{cf['inflow']:,.0f} in {month} has no corresponding trades",
                    details={
                        'month': month,
                        'inflow': float(cf['inflow']),
                        'outflow': float(cf['outflow']),
                        'buy_amount': float(tr['buy']),
                        'sell_amount': float(tr['sell'])
                    },
                    suggested_action="Verify if funds are pending investment or recorded elsewhere",
                    group_key=f"CASHFLOW_orphan_{month}"
                )
                if issue:
                    issues.append(issue)
        
        return issues
    
    # =========================================================================
    # CHECK 3: CORPORATE ACTION VERIFICATION
    # NOTE: This check is DISABLED - forward calculation handles corporate actions
    # automatically. Holdings are not stored but calculated on-the-fly.
    # =========================================================================
    
    def check_corporate_actions(self, client_id: int, since: datetime = None) -> List[DataIntegrityIssue]:
        """
        Verify corporate actions are properly applied to holdings.
        
        NOTE: This check is DISABLED - forward calculation handles corporate
        actions automatically. Holdings are calculated on-the-fly, not stored.
        
        Checks:
        - Unapplied corporate actions
        - Quantity changes without corporate action record
        """
        issues = []
        
        # Get client holdings
        holdings = Holding.query.filter_by(client_id=client_id).all()
        security_ids = [h.security_id for h in holdings]
        
        if not security_ids:
            return issues
        
        # Get corporate actions for held securities
        ca_query = CorporateAction.query.filter(
            CorporateAction.security_id.in_(security_ids),
            CorporateAction.is_active == True
        )
        
        if since:
            ca_query = ca_query.filter(CorporateAction.action_date >= since.date())
        
        corporate_actions = ca_query.all()
        
        for ca in corporate_actions:
            # Find holding for this security
            holding = next((h for h in holdings if h.security_id == ca.security_id), None)
            if not holding:
                continue
            
            # Check if there's a transaction around the corporate action date
            # that reflects the adjustment
            ca_transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == ca.security_id,
                Transaction.transaction_date >= ca.action_date,
                Transaction.transaction_date <= ca.action_date + timedelta(days=7)
            ).all()
            
            # For splits/bonuses, expect a transaction with the adjustment
            if ca.action_type in ('SPLIT', 'BONUS'):
                has_adjustment = any(
                    'bonus' in (t.notes or '').lower() or 
                    'split' in (t.notes or '').lower() or
                    t.price == 0  # Often corporate actions have 0 price
                    for t in ca_transactions
                )
                
                if not has_adjustment:
                    issue = self.create_issue(
                        client_id=client_id,
                        check_category='CORPORATE_ACTION',
                        check_name='unapplied_action',
                        severity='critical',
                        message=f"{ca.action_type} of {ca.ratio} for {ca.security.symbol if ca.security else 'Unknown'} "
                                f"on {ca.action_date} may not be applied",
                        details={
                            'corporate_action_id': ca.id,
                            'security_id': ca.security_id,
                            'security_symbol': ca.security.symbol if ca.security else None,
                            'action_type': ca.action_type,
                            'action_date': ca.action_date.isoformat(),
                            'ratio': float(ca.ratio),
                            'holding_quantity': float(holding.quantity)
                        },
                        suggested_action=f"Verify that {ca.action_type} with ratio {ca.ratio} is applied to holdings",
                        security_id=ca.security_id,
                        reference_date=ca.action_date
                    )
                    if issue:
                        issues.append(issue)
        
        return issues
    
    # =========================================================================
    # CHECK 4: HOLDINGS VS TRANSACTIONS RECONCILIATION
    # NOTE: This check is DISABLED because holdings are calculated on-the-fly
    # using the forward calc method, not stored in the database.
    # Use Portfolio Construction for spot-checking instead.
    # =========================================================================
    
    def check_holdings_vs_transactions(self, client_id: int) -> List[DataIntegrityIssue]:
        """
        Verify holdings match the sum of transactions.
        
        Holding.quantity should equal Sum(buy_qty) - Sum(sell_qty)
        
        NOTE: This check is currently DISABLED - holdings are calculated 
        on-the-fly via forward calc, not stored. Use Portfolio Construction
        for spot checks.
        """
        issues = []
        
        holdings = Holding.query.filter_by(client_id=client_id).all()
        
        for holding in holdings:
            # Calculate expected quantity from transactions
            buy_qty = db.session.query(func.sum(Transaction.quantity)).filter(
                Transaction.client_id == client_id,
                Transaction.security_id == holding.security_id,
                Transaction.type.in_(['BUY', 'Buy', 'buy'])
            ).scalar() or Decimal(0)
            
            sell_qty = db.session.query(func.sum(Transaction.quantity)).filter(
                Transaction.client_id == client_id,
                Transaction.security_id == holding.security_id,
                Transaction.type.in_(['SELL', 'Sell', 'sell'])
            ).scalar() or Decimal(0)
            
            expected_qty = float(buy_qty) - float(sell_qty)
            actual_qty = float(holding.quantity)
            
            # Allow small rounding differences
            if abs(expected_qty - actual_qty) > 0.01:
                variance_pct = abs(expected_qty - actual_qty) / max(abs(expected_qty), abs(actual_qty), 1) * 100
                
                issue = self.create_issue(
                    client_id=client_id,
                    check_category='HOLDINGS',
                    check_name='quantity_mismatch',
                    severity='critical' if variance_pct > 5 else 'warning',
                    message=f"Holding quantity mismatch for {holding.security.symbol if holding.security else 'Unknown'}: "
                            f"expected {expected_qty:.2f} (from transactions), actual {actual_qty:.2f}",
                    details={
                        'holding_id': holding.id,
                        'security_id': holding.security_id,
                        'security_symbol': holding.security.symbol if holding.security else None,
                        'expected_qty': expected_qty,
                        'actual_qty': actual_qty,
                        'buy_total': float(buy_qty),
                        'sell_total': float(sell_qty),
                        'difference': actual_qty - expected_qty,
                        'variance_pct': variance_pct
                    },
                    suggested_action="Check for missing transactions, corporate actions, or manual adjustments",
                    security_id=holding.security_id
                )
                if issue:
                    issues.append(issue)
        
        return issues
    
    # =========================================================================
    # CHECK 5: DUPLICATE DETECTION
    # =========================================================================
    
    def check_duplicates(self, client_id: int, since: datetime = None) -> List[DataIntegrityIssue]:
        """
        Detect potential duplicate transactions.
        Also auto-resolves open DUPLICATES issues whose exact match is gone.
        """
        issues = []
        
        txn_query = Transaction.query.filter_by(client_id=client_id)
        if since:
            txn_query = txn_query.filter(Transaction.transaction_date >= since)
        
        transactions = txn_query.order_by(Transaction.transaction_date).all()
        
        # Group by date + security
        groups = {}
        for txn in transactions:
            key = (txn.transaction_date.date(), txn.security_id, txn.type)
            if key not in groups:
                groups[key] = []
            groups[key].append(txn)
        
        # Check for exact duplicates
        for key, txns in groups.items():
            if len(txns) < 2:
                continue
            
            # Check for exact matches (same qty, price, amount)
            seen = set()
            for txn in txns:
                sig = (float(txn.quantity), float(txn.price), float(txn.amount))
                if sig in seen:
                    issue = self.create_issue(
                        client_id=client_id,
                        check_category='DUPLICATES',
                        check_name='duplicate_transaction',
                        severity='critical',
                        message=f"Potential duplicate transaction for {txn.security.symbol if txn.security else 'Unknown'} "
                                f"on {txn.transaction_date.strftime('%Y-%m-%d')}: "
                                f"{txn.type} {txn.quantity} @ ₹{txn.price}",
                        details={
                            'transaction_id': txn.id,
                            'security_id': txn.security_id,
                            'security_symbol': txn.security.symbol if txn.security else None,
                            'date': txn.transaction_date.isoformat(),
                            'type': txn.type,
                            'quantity': float(txn.quantity),
                            'price': float(txn.price),
                            'amount': float(txn.amount),
                            'similar_transactions': [t.id for t in txns]
                        },
                        suggested_action="Review and delete if duplicate",
                        transaction_id=txn.id,
                        security_id=txn.security_id,
                        reference_date=txn.transaction_date.date()
                    )
                    if issue:
                        issues.append(issue)
                seen.add(sig)

        self._reconcile_stale_duplicate_issues(client_id)
        return issues

    def _reconcile_stale_duplicate_issues(self, client_id: int) -> int:
        """
        Resolve open duplicate_transaction issues when fewer than two exact matches remain.
        Uses the shared root-cause evaluator + lifecycle sync (tasks/alerts).
        """
        from services.issue_lifecycle_service import resolve_issue_and_sync
        from services.issue_root_cause_evaluator import evaluate_issue_root_cause

        open_issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "DUPLICATES",
            DataIntegrityIssue.check_name == "duplicate_transaction",
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        ).all()
        cleared = 0
        for issue in open_issues:
            evaluation = evaluate_issue_root_cause(issue)
            if not evaluation.get("is_cleared"):
                continue
            resolve_issue_and_sync(
                issue,
                resolution_type="auto_cleared",
                actor_user_id=None,
                notes=evaluation.get("reason")
                or "Auto-cleared: exact duplicate trade no longer present",
                source="data_integrity_manager",
                commit=False,
            )
            cleared += 1
            logger.info(
                "Auto-resolved stale duplicate issue %s for client %s: %s",
                issue.id,
                client_id,
                evaluation.get("reason"),
            )
        return cleared
    
    # =========================================================================
    # CHECK 6: DATE INTEGRITY
    # =========================================================================
    
    def check_date_integrity(self, client_id: int, since: datetime = None) -> List[DataIntegrityIssue]:
        """
        Check for date-related issues.
        
        Checks:
        - Future dated transactions
        - Transactions before client onboarding
        """
        issues = []
        
        client = Client.query.get(client_id)
        if not client:
            return issues
        
        today = datetime.utcnow().date()
        
        # Check for future dated transactions
        future_txns = Transaction.query.filter(
            Transaction.client_id == client_id,
            func.date(Transaction.transaction_date) > today
        ).all()
        
        for txn in future_txns:
            issue = self.create_issue(
                client_id=client_id,
                check_category='DATE_INTEGRITY',
                check_name='future_date',
                severity='critical',
                message=f"Transaction for {txn.security.symbol if txn.security else 'Unknown'} "
                        f"has future date: {txn.transaction_date.strftime('%Y-%m-%d')}",
                details={
                    'transaction_id': txn.id,
                    'security_id': txn.security_id,
                    'security_symbol': txn.security.symbol if txn.security else None,
                    'transaction_date': txn.transaction_date.isoformat(),
                    'today': today.isoformat()
                },
                suggested_action="Correct the transaction date",
                transaction_id=txn.id,
                security_id=txn.security_id,
                reference_date=txn.transaction_date.date()
            )
            if issue:
                issues.append(issue)
        
        return issues
    
    # =========================================================================
    # CHECK 7: NEGATIVE VALUES
    # =========================================================================
    
    def check_negative_values(self, client_id: int) -> List[DataIntegrityIssue]:
        """
        Check for invalid negative values.
        """
        issues = []
        
        # Check holdings with negative quantity
        negative_holdings = Holding.query.filter(
            Holding.client_id == client_id,
            Holding.quantity < 0
        ).all()
        
        for holding in negative_holdings:
            issue = self.create_issue(
                client_id=client_id,
                check_category='NEGATIVE_VALUES',
                check_name='negative_holding',
                severity='critical',
                message=f"Negative holding quantity for {holding.security.symbol if holding.security else 'Unknown'}: "
                        f"{holding.quantity}",
                details={
                    'holding_id': holding.id,
                    'security_id': holding.security_id,
                    'security_symbol': holding.security.symbol if holding.security else None,
                    'quantity': float(holding.quantity)
                },
                suggested_action="Review transactions to fix holding quantity",
                security_id=holding.security_id
            )
            if issue:
                issues.append(issue)
        
        # Check transactions with negative price
        negative_price_txns = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.price < 0
        ).all()
        
        for txn in negative_price_txns:
            issue = self.create_issue(
                client_id=client_id,
                check_category='NEGATIVE_VALUES',
                check_name='negative_price',
                severity='critical',
                message=f"Negative price for {txn.security.symbol if txn.security else 'Unknown'}: ₹{txn.price}",
                details={
                    'transaction_id': txn.id,
                    'security_id': txn.security_id,
                    'security_symbol': txn.security.symbol if txn.security else None,
                    'price': float(txn.price)
                },
                suggested_action="Correct the transaction price",
                transaction_id=txn.id,
                security_id=txn.security_id
            )
            if issue:
                issues.append(issue)
        
        return issues
