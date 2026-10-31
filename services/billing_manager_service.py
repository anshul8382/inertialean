"""
Billing Manager Service
-----------------------
On-request planner to prioritize due billings, maximize billed value,
and produce daily/weekly/monthly catch-up plans.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from calendar import monthrange

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from billing_calculation_service import BillingCalculationService
from models import Agreement, BillingSchedule, Invoice, Lead

logger = logging.getLogger(__name__)


@dataclass
class BillingInputFlag:
    code: str
    severity: str  # hard | soft
    message: str


class BillingManagerService:
    """Manager-like planner for billing backlog and catch-up roadmap."""

    ACTIVE_AGREEMENT_STATUSES = {"signed", "active", "completed"}
    FREQ_MONTHS = {
        "monthly": 1,
        "quarterly": 3,
        "half_yearly": 6,
        "yearly": 12,
    }
    OPEN_CYCLE_STATUSES = frozenset({"draft", "sent", "overdue"})
    OUTSTANDING_STATUSES = frozenset({"sent", "overdue"})

    @classmethod
    def run_plan(
        cls,
        as_of_date: Optional[date] = None,
        weekly_capacity: int = 5,
        value_weight: float = 0.7,
        overdue_weight: float = 0.3,
        accessible_client_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        as_of = as_of_date or date.today()
        # Ops capacity: how many due billings ops can process per week (no artificial 5 cap).
        capacity = max(1, min(int(weekly_capacity or 5), 50))
        if value_weight < 0:
            value_weight = 0.0
        if overdue_weight < 0:
            overdue_weight = 0.0
        if value_weight + overdue_weight == 0:
            value_weight, overdue_weight = 0.7, 0.3

        allowed_clients = set(accessible_client_ids or [])
        enforce_access = bool(accessible_client_ids is not None)

        agreements = (
            Agreement.query.options(
                joinedload(Agreement.lead).joinedload(Lead.client),
                joinedload(Agreement.variables),
                joinedload(Agreement.billing_schedules),
            )
            .filter(func.lower(Agreement.status).in_(list(cls.ACTIVE_AGREEMENT_STATUSES)))
            .all()
        )

        # Prefetch invoices for clients in scope (+ household peers).
        client_ids_for_prefetch: List[int] = []
        for ag in agreements:
            lead = ag.lead
            client = lead.client if lead and getattr(lead, "client", None) else None
            if client is None:
                continue
            if enforce_access and client.id not in allowed_clients:
                continue
            client_ids_for_prefetch.append(client.id)

        hh_ctx = cls._load_household_context(client_ids_for_prefetch)
        peers_by_client = hh_ctx["peers_by_client"]
        meta_by_client = hh_ctx["meta_by_client"]
        expanded_ids: List[int] = []
        for cid in client_ids_for_prefetch:
            expanded_ids.extend(peers_by_client.get(cid, [cid]))
        invoices_by_client = cls._load_invoices_by_client(expanded_ids)

        opportunities: List[Dict[str, Any]] = []
        for ag in agreements:
            lead = ag.lead
            client = lead.client if lead and getattr(lead, "client", None) else None
            client_id = client.id if client else None
            if enforce_access and (client_id is None or client_id not in allowed_clients):
                continue
            peer_ids = peers_by_client.get(client_id or -1, [client_id] if client_id else [])
            inv_rows: List[Invoice] = []
            for pid in peer_ids:
                inv_rows.extend(invoices_by_client.get(pid, []))
            inv_rows.sort(
                key=lambda inv: cls._coerce_date(inv.invoice_date) or date.min,
                reverse=True,
            )
            hh_meta = meta_by_client.get(client_id or -1) or {}
            opp = cls._build_opportunity(
                ag,
                as_of,
                invoices=inv_rows,
                household_meta=hh_meta,
                household_peer_ids=peer_ids,
            )
            opportunities.append(opp)

        # One billing queue entry per household when several members still look due.
        cls._dedupe_household_due_opportunities(opportunities)

        missing_inputs: List[Dict[str, Any]] = []
        for opp in opportunities:
            if opp.get("flags"):
                missing_inputs.append(
                    {
                        "agreement_id": opp["agreement_id"],
                        "client_id": opp["client_id"],
                        "client_name": opp["client_name"],
                        "flags": opp["flags"],
                    }
                )

        due_candidates = [
            o
            for o in opportunities
            if o["is_due"]
            and not o["has_hard_block"]
            and not o.get("exclude_from_due_plan")
            and o["estimated_invoice_value"] > 0
        ]
        due_ranked = cls._rank_due(due_candidates, value_weight, overdue_weight)

        daily_plan = due_ranked[:capacity]
        weekly_plan = due_ranked[:capacity]
        roadmap = cls._build_weekly_roadmap(due_ranked, capacity, as_of)
        upcoming = cls._build_upcoming(opportunities, as_of, horizon_days=30)
        status_report = cls._build_status_report(opportunities)

        total_due_value = round(sum(x["estimated_invoice_value"] for x in due_ranked), 2)
        total_outstanding = round(
            sum(float(x.get("outstanding_unpaid") or 0) for x in due_ranked), 2
        )
        due_count = len(due_ranked)
        weeks_to_catch_up = (due_count + capacity - 1) // capacity if due_count else 0

        catch_up_week = None
        if roadmap:
            catch_up_week = roadmap[-1]

        missing_details = status_report["missing_details"]
        ops_client_ids = sorted(
            {
                int(o["client_id"])
                for o in opportunities
                if o.get("client_id") is not None
            }
            | set(int(i) for i in (accessible_client_ids or []) if i)
        )
        try:
            from services.billing_ops_reports_service import build_billing_ops_reports

            ops_reports = build_billing_ops_reports(
                accessible_client_ids=ops_client_ids,
                as_of=as_of,
                status_report=status_report,
            )
        except Exception as exc:
            logger.warning("ops reports failed: %s", exc)
            ops_reports = None

        return {
            "as_of_date": as_of.isoformat(),
            "parameters": {
                "weekly_capacity": capacity,
                "value_weight": value_weight,
                "overdue_weight": overdue_weight,
            },
            "summary": {
                "active_agreements_scanned": len(opportunities),
                "due_billings": due_count,
                "total_due_estimated_value": total_due_value,
                "total_outstanding_unpaid": total_outstanding,
                "weeks_to_catch_up": weeks_to_catch_up,
                "catch_up_by_week": catch_up_week["week_index"] if catch_up_week else None,
                "catch_up_by_week_end": catch_up_week["week_end"] if catch_up_week else None,
                "caught_up_count": len(status_report["caught_up"]),
                "lagging_count": len(status_report["lagging"]),
                "already_covered_count": len(status_report["already_covered"]),
                "missing_details_count": len(missing_details),
                "reviews_on_track_count": (ops_reports or {})
                .get("summary", {})
                .get("reviews_on_track"),
                "missing_agreements_count": (ops_reports or {})
                .get("summary", {})
                .get("missing_agreements"),
            },
            "daily_plan": daily_plan,
            "weekly_plan": weekly_plan,
            "monthly_projection": {
                "upcoming_30_days": upcoming,
                "roadmap_by_week": roadmap,
            },
            "status_report": status_report,
            "ops_reports": ops_reports,
            "missing_inputs": missing_inputs,
            "missing_details": missing_details,
        }

    @classmethod
    def _load_household_context(cls, client_ids: List[int]) -> Dict[str, Any]:
        """
        Map each client to household peer ids + meta.

        Families share one billing cycle — invoices on Ajith count for Anjali.
        """
        unique = sorted({int(c) for c in client_ids if c})
        peers_by_client: Dict[int, List[int]] = {cid: [cid] for cid in unique}
        meta_by_client: Dict[int, Dict[str, Any]] = {}
        if not unique:
            return {"peers_by_client": peers_by_client, "meta_by_client": meta_by_client}
        try:
            from extensions import db
            from models import ClientGroup, ClientGroupMember

            insp = db.inspect(db.engine)
            if not insp.has_table("client_group_member") or not insp.has_table("client_group"):
                return {"peers_by_client": peers_by_client, "meta_by_client": meta_by_client}

            seed = ClientGroupMember.query.filter(
                ClientGroupMember.client_id.in_(unique)
            ).all()
            group_ids = {m.group_id for m in seed}
            if not group_ids:
                return {"peers_by_client": peers_by_client, "meta_by_client": meta_by_client}

            all_members = ClientGroupMember.query.filter(
                ClientGroupMember.group_id.in_(list(group_ids))
            ).all()
            groups = {
                g.id: g
                for g in ClientGroup.query.filter(ClientGroup.id.in_(list(group_ids))).all()
            }
            by_group: Dict[int, List[Any]] = defaultdict(list)
            for m in all_members:
                by_group[m.group_id].append(m)

            for gid, mems in by_group.items():
                g = groups.get(gid)
                ids = sorted({m.client_id for m in mems})
                for m in mems:
                    peers_by_client[m.client_id] = ids
                    meta_by_client[m.client_id] = {
                        "household_id": gid,
                        "household_name": g.name if g else None,
                        "household_role": m.role,
                        "household_primary_client_id": g.primary_client_id if g else None,
                    }
        except Exception as exc:
            logger.warning("Household context load failed: %s", exc)
        return {"peers_by_client": peers_by_client, "meta_by_client": meta_by_client}

    @classmethod
    def _dedupe_household_due_opportunities(cls, opportunities: List[Dict[str, Any]]) -> None:
        """Keep one due queue row per household (primary, else highest estimate)."""
        by_hh: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for o in opportunities:
            hh = o.get("household_id")
            if not hh:
                continue
            if (
                o.get("is_due")
                and not o.get("has_hard_block")
                and not o.get("exclude_from_due_plan")
                and float(o.get("estimated_invoice_value") or 0) > 0
            ):
                by_hh[int(hh)].append(o)

        for _hh, members in by_hh.items():
            if len(members) <= 1:
                continue

            def _keep_key(o: Dict[str, Any]) -> Tuple[int, float, int]:
                is_primary = (
                    1
                    if o.get("client_id")
                    and o.get("client_id") == o.get("household_primary_client_id")
                    else 0
                )
                return (
                    is_primary,
                    float(o.get("estimated_invoice_value") or 0),
                    int(o.get("days_overdue") or 0),
                )

            members_sorted = sorted(members, key=_keep_key, reverse=True)
            keep = members_sorted[0]
            for o in members_sorted[1:]:
                o["exclude_from_due_plan"] = True
                o["household_covered_by_client_id"] = keep.get("client_id")
                o["household_covered_by_name"] = keep.get("client_name")
                flags = list(o.get("flags") or [])
                flags.append(
                    {
                        "code": "HOUSEHOLD_BILLED_WITH_PEER",
                        "severity": "soft",
                        "message": (
                            f"Household '{o.get('household_name') or 'family'}' billing "
                            f"queued on {keep.get('client_name')} — skipped duplicate."
                        ),
                    }
                )
                o["flags"] = flags

    @classmethod
    def _load_invoices_by_client(
        cls, client_ids: List[int]
    ) -> Dict[int, List[Invoice]]:
        if not client_ids:
            return {}
        unique_ids = sorted(set(client_ids))
        rows = (
            Invoice.query.filter(
                Invoice.client_id.in_(unique_ids),
                Invoice.status != "cancelled",
            )
            .order_by(Invoice.invoice_date.desc())
            .all()
        )
        out: Dict[int, List[Invoice]] = {}
        for inv in rows:
            out.setdefault(inv.client_id, []).append(inv)
        return out

    @classmethod
    def _invoice_is_zoho(cls, inv: Invoice) -> bool:
        notes = inv.notes or ""
        if "[zoho_invoice_id=" in notes:
            return True
        # Pulled imports also start with this prefix before marker rewrite.
        return notes.lower().startswith("pulled from zoho")

    @classmethod
    def _latest_date_from_invoices(
        cls, invoices: List[Invoice]
    ) -> Tuple[Optional[date], Optional[int]]:
        """Most recent invoice date + owning client_id (household-aware list)."""
        best_d: Optional[date] = None
        best_cid: Optional[int] = None
        for inv in invoices:
            idate = cls._coerce_date(inv.invoice_date)
            if idate is None:
                continue
            if best_d is None or idate > best_d:
                best_d = idate
                best_cid = inv.client_id
        return best_d, best_cid

    @classmethod
    def _signals_from_invoices(
        cls,
        invoices: List[Invoice],
        *,
        agreement_id: int,
        next_billing: date,
        last_billed: Optional[date],
        as_of: date,
        household_mode: bool = False,
        self_client_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Derive Zoho/open/outstanding signals from local invoices (incl. pulled)."""
        if household_mode:
            # Family billing: any member's invoice counts (often under one name).
            relevant = list(invoices)
        else:
            relevant = [
                inv
                for inv in invoices
                if inv.agreement_id in (None, agreement_id)
            ]
        last_inertia: Optional[date] = None
        last_inertia_client_id: Optional[int] = None
        last_zoho: Optional[date] = None
        last_zoho_client_id: Optional[int] = None
        outstanding = 0.0
        open_cycle_invoice_id: Optional[int] = None
        open_cycle_number: Optional[str] = None
        open_cycle_status: Optional[str] = None
        open_cycle_client_id: Optional[int] = None
        period_invoice_id: Optional[int] = None
        period_invoice_number: Optional[str] = None
        period_invoice_status: Optional[str] = None
        period_invoice_client_id: Optional[int] = None
        period_from_zoho = False

        for inv in relevant:
            idate = cls._coerce_date(inv.invoice_date)
            if idate is None:
                continue
            is_zoho = cls._invoice_is_zoho(inv)
            if last_inertia is None:
                last_inertia = idate
                last_inertia_client_id = inv.client_id
            if is_zoho and last_zoho is None:
                last_zoho = idate
                last_zoho_client_id = inv.client_id

            status = (inv.status or "").lower()
            if status in cls.OUTSTANDING_STATUSES:
                amt = float(inv.total_amount or 0)
                # Treat sent past due_date as overdue for collections ranking.
                if status == "sent" and inv.due_date and cls._coerce_date(inv.due_date) < as_of:
                    outstanding += amt
                elif status == "overdue":
                    outstanding += amt
                elif status == "sent":
                    outstanding += amt

            # Already invoiced for this billing cycle (on/after due date).
            if idate >= next_billing and period_invoice_id is None:
                period_invoice_id = inv.id
                period_invoice_number = inv.invoice_number
                period_invoice_status = status
                period_invoice_client_id = inv.client_id
                period_from_zoho = is_zoho

            # Open draft/sent for the cycle — do not queue another generate.
            cycle_cutoff = last_billed or (next_billing - timedelta(days=1))
            if (
                status in cls.OPEN_CYCLE_STATUSES
                and idate > cycle_cutoff
                and open_cycle_invoice_id is None
            ):
                open_cycle_invoice_id = inv.id
                open_cycle_number = inv.invoice_number
                open_cycle_status = status
                open_cycle_client_id = inv.client_id

        period_covered = period_invoice_id is not None
        has_open_cycle = open_cycle_invoice_id is not None
        peer_billed = bool(
            self_client_id
            and last_inertia_client_id
            and last_inertia_client_id != self_client_id
        )
        return {
            "last_inertia_invoice_date": last_inertia.isoformat() if last_inertia else None,
            "last_inertia_invoice_client_id": last_inertia_client_id,
            "last_zoho_invoice_date": last_zoho.isoformat() if last_zoho else None,
            "last_zoho_invoice_client_id": last_zoho_client_id,
            "outstanding_unpaid": round(outstanding, 2),
            "period_covered": period_covered,
            "period_invoice_id": period_invoice_id,
            "period_invoice_number": period_invoice_number,
            "period_invoice_status": period_invoice_status,
            "period_invoice_client_id": period_invoice_client_id,
            "period_invoice_from_zoho": period_from_zoho,
            "has_open_cycle_invoice": has_open_cycle,
            "open_cycle_invoice_id": open_cycle_invoice_id,
            "open_cycle_invoice_number": open_cycle_number,
            "open_cycle_invoice_status": open_cycle_status,
            "open_cycle_invoice_client_id": open_cycle_client_id,
            "household_peer_billed": peer_billed,
            "exclude_from_due_plan": period_covered or has_open_cycle,
        }

    @classmethod
    def _build_opportunity(
        cls,
        ag: Agreement,
        as_of: date,
        invoices: Optional[List[Invoice]] = None,
        household_meta: Optional[Dict[str, Any]] = None,
        household_peer_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        flags: List[BillingInputFlag] = []
        lead = ag.lead
        client = lead.client if lead and getattr(lead, "client", None) else None
        client_name = client.name if client else "Unknown"
        client_id = client.id if client else None
        zoho_mapped = bool(
            client is not None and (getattr(client, "zoho_contact_id", None) or "").strip()
        )
        hh = household_meta or {}
        household_mode = bool(hh.get("household_id")) and len(household_peer_ids or []) > 1

        if client is None:
            flags.append(
                BillingInputFlag(
                    "MISSING_CLIENT_LINK", "hard", "Agreement does not resolve to a client."
                )
            )

        frequency, freq_defaulted = cls._extract_frequency(ag)
        if freq_defaulted:
            flags.append(
                BillingInputFlag(
                    "FREQUENCY_DEFAULTED",
                    "soft",
                    "billing_frequency missing; defaulted to yearly.",
                )
            )

        schedule = cls._active_schedule(ag)
        if not schedule:
            flags.append(
                BillingInputFlag(
                    "MISSING_BILLING_SCHEDULE",
                    "hard",
                    "No active billing schedule for agreement.",
                )
            )

        inv_list = list(invoices or [])
        # Household: last billed = latest invoice across family members (any agreement).
        latest_invoice_date, latest_via_client_id = cls._latest_date_from_invoices(inv_list)
        if latest_invoice_date is None:
            latest_invoice_date = cls._latest_invoice_date(ag.id, client_id=client_id)
            latest_via_client_id = client_id
        last_billed = cls._effective_last_billed(
            schedule.last_billing_date if schedule else None,
            latest_invoice_date,
        )

        next_billing = cls._resolve_next_billing_date(
            schedule=schedule,
            last_billed=last_billed,
            frequency=frequency,
            agreement=ag,
            as_of=as_of,
        )

        stored_next = schedule.next_billing_date if schedule else None
        if last_billed and stored_next and stored_next <= last_billed:
            flags.append(
                BillingInputFlag(
                    "SCHEDULE_INCONSISTENT",
                    "soft",
                    "Stored next_billing_date is on or before last billed; using last invoice + frequency.",
                )
            )

        estimate = 0.0
        estimate_error = None
        excluded_assets: List[str] = []
        if client is not None:
            calc = BillingCalculationService.calculate_billing_amount(ag.id, as_of)
            if "error" in calc:
                estimate_error = str(calc["error"])
                flags.extend(cls._flags_from_calc_error(estimate_error))
            else:
                totals = calc.get("totals") or {}
                estimate = float(totals.get("total_amount") or 0.0)
                portfolio_summary = calc.get("portfolio_summary") or {}
                excluded_assets = portfolio_summary.get("excluded_assets") or []
                if excluded_assets:
                    flags.append(
                        BillingInputFlag(
                            "ASSET_NOT_MAPPED",
                            "soft",
                            f"{len(excluded_assets)} holding(s) excluded due to missing asset mapping/price.",
                        )
                    )
                if estimate <= 0:
                    flags.append(
                        BillingInputFlag(
                            "NO_BILLABLE_ASSETS",
                            "hard",
                            "Calculated invoice value is zero.",
                        )
                    )

        signals = cls._signals_from_invoices(
            inv_list,
            agreement_id=ag.id,
            next_billing=next_billing,
            last_billed=last_billed,
            as_of=as_of,
            household_mode=household_mode,
            self_client_id=client_id,
        )
        if (
            household_mode
            and latest_via_client_id
            and client_id
            and latest_via_client_id != client_id
        ):
            flags.append(
                BillingInputFlag(
                    "HOUSEHOLD_PEER_INVOICE",
                    "soft",
                    "Last household invoice is on another family member — treated as shared billing.",
                )
            )
        if signals["period_covered"]:
            src = "Zoho" if signals["period_invoice_from_zoho"] else "Inertia"
            peer_note = ""
            if (
                signals.get("period_invoice_client_id")
                and client_id
                and signals["period_invoice_client_id"] != client_id
            ):
                peer_note = " (household peer)"
            flags.append(
                BillingInputFlag(
                    "PERIOD_ALREADY_INVOICED",
                    "soft",
                    f"Cycle already invoiced{peer_note} ({src} {signals['period_invoice_number']}, "
                    f"status={signals['period_invoice_status']}).",
                )
            )
        elif signals["has_open_cycle_invoice"]:
            flags.append(
                BillingInputFlag(
                    "OPEN_INVOICE_THIS_CYCLE",
                    "soft",
                    f"Open {signals['open_cycle_invoice_status']} invoice "
                    f"{signals['open_cycle_invoice_number']} — excluded from new-bill queue.",
                )
            )
        if signals["outstanding_unpaid"] > 0:
            flags.append(
                BillingInputFlag(
                    "OUTSTANDING_UNPAID",
                    "soft",
                    f"Unpaid sent/overdue invoices totaling ₹{signals['outstanding_unpaid']:,.0f}.",
                )
            )

        days_overdue = max((as_of - next_billing).days, 0)
        is_due = next_billing <= as_of

        hard_block = any(f.severity == "hard" for f in flags)
        # Zoho mapped if self or household peer has a Zoho contact id.
        if not zoho_mapped and household_mode and client_id:
            try:
                from services.zoho_invoice_pull_service import get_household_zoho_mapping

                zoho_mapped = bool(get_household_zoho_mapping(client))
            except Exception:
                pass

        return {
            "agreement_id": ag.id,
            "client_id": client_id,
            "client_name": client_name,
            "household_id": hh.get("household_id"),
            "household_name": hh.get("household_name"),
            "household_role": hh.get("household_role"),
            "household_primary_client_id": hh.get("household_primary_client_id"),
            "last_billed_via_client_id": latest_via_client_id,
            "zoho_mapped": zoho_mapped,
            "last_billed_date": last_billed.isoformat() if last_billed else None,
            "last_inertia_invoice_date": signals["last_inertia_invoice_date"],
            "last_zoho_invoice_date": signals["last_zoho_invoice_date"],
            "next_billing_date": next_billing.isoformat(),
            "billing_frequency": frequency,
            "is_due": is_due,
            "days_overdue": days_overdue,
            "estimated_invoice_value": round(estimate, 2),
            "outstanding_unpaid": signals["outstanding_unpaid"],
            "period_covered": signals["period_covered"],
            "has_open_cycle_invoice": signals["has_open_cycle_invoice"],
            "exclude_from_due_plan": signals["exclude_from_due_plan"],
            "period_invoice_id": signals["period_invoice_id"],
            "open_cycle_invoice_id": signals["open_cycle_invoice_id"],
            "estimate_error": estimate_error,
            "excluded_assets": excluded_assets[:20],
            "has_hard_block": hard_block,
            "flags": [
                {"code": f.code, "severity": f.severity, "message": f.message}
                for f in flags
            ],
        }

    @classmethod
    def _status_row(cls, o: Dict[str, Any]) -> Dict[str, Any]:
        hard_flags = [f for f in (o.get("flags") or []) if f.get("severity") == "hard"]
        return {
            "agreement_id": o.get("agreement_id"),
            "client_id": o.get("client_id"),
            "client_name": o.get("client_name"),
            "zoho_mapped": o.get("zoho_mapped"),
            "household_id": o.get("household_id"),
            "household_name": o.get("household_name"),
            "household_covered_by_name": o.get("household_covered_by_name"),
            "last_billed_date": o.get("last_billed_date"),
            "last_inertia_invoice_date": o.get("last_inertia_invoice_date"),
            "last_zoho_invoice_date": o.get("last_zoho_invoice_date"),
            "next_billing_date": o.get("next_billing_date"),
            "days_overdue": o.get("days_overdue", 0),
            "estimated_invoice_value": o.get("estimated_invoice_value", 0),
            "outstanding_unpaid": o.get("outstanding_unpaid", 0),
            "billing_frequency": o.get("billing_frequency"),
            "period_covered": o.get("period_covered"),
            "has_open_cycle_invoice": o.get("has_open_cycle_invoice"),
            "flags": o.get("flags") or [],
            "hard_flag_codes": [f.get("code") for f in hard_flags],
            "hard_flag_messages": [f.get("message") for f in hard_flags],
        }

    @classmethod
    def _build_status_report(cls, opportunities: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bucket scanned agreements:

        - missing_details: hard block (cannot bill)
        - already_covered: due by date but period already invoiced / open draft|sent
        - lagging: due, billable, not covered
        - caught_up: next billing still in the future and not hard-blocked
        """
        lagging: List[Dict[str, Any]] = []
        caught_up: List[Dict[str, Any]] = []
        already_covered: List[Dict[str, Any]] = []
        missing_details: List[Dict[str, Any]] = []
        for o in opportunities:
            row = cls._status_row(o)
            if o.get("has_hard_block"):
                missing_details.append(row)
            elif o.get("exclude_from_due_plan"):
                already_covered.append(row)
            elif o.get("is_due"):
                lagging.append(row)
            else:
                caught_up.append(row)

        lagging.sort(
            key=lambda x: (
                -float(x.get("outstanding_unpaid") or 0),
                -x["days_overdue"],
                -x["estimated_invoice_value"],
                x["client_name"] or "",
            )
        )
        caught_up.sort(key=lambda x: (x["next_billing_date"] or "", x["client_name"] or ""))
        already_covered.sort(key=lambda x: (x["client_name"] or "", x["agreement_id"] or 0))
        missing_details.sort(key=lambda x: (x["client_name"] or "", x["agreement_id"] or 0))
        return {
            "lagging": lagging,
            "caught_up": caught_up,
            "already_covered": already_covered,
            "missing_details": missing_details,
        }

    @classmethod
    def _rank_due(
        cls,
        due_items: List[Dict[str, Any]],
        value_weight: float,
        overdue_weight: float,
    ) -> List[Dict[str, Any]]:
        if not due_items:
            return []
        # Rank value blends fee estimate with unpaid Zoho/Inertia invoices (collections).
        def _rank_value(x: Dict[str, Any]) -> float:
            return float(x.get("estimated_invoice_value") or 0) + float(
                x.get("outstanding_unpaid") or 0
            )

        max_value = max(_rank_value(x) for x in due_items) or 1.0
        max_days = max(x["days_overdue"] for x in due_items) or 1
        denom = value_weight + overdue_weight
        ranked = []
        for item in due_items:
            value_norm = _rank_value(item) / max_value
            overdue_norm = item["days_overdue"] / max_days
            score = ((value_weight * value_norm) + (overdue_weight * overdue_norm)) / denom
            r = dict(item)
            r["priority_score"] = round(score, 4)
            outstanding = float(item.get("outstanding_unpaid") or 0)
            if outstanding > 0 and outstanding >= float(item.get("estimated_invoice_value") or 0):
                r["selection_reason"] = "collections_outstanding"
            elif overdue_norm > 0.75 and value_norm < 0.25:
                r["selection_reason"] = "severely_overdue"
            elif value_norm >= 0.75:
                r["selection_reason"] = "high_value_due"
            else:
                r["selection_reason"] = "balanced_priority"
            ranked.append(r)
        ranked.sort(
            key=lambda x: (
                -x["priority_score"],
                -float(x.get("outstanding_unpaid") or 0),
                -x["estimated_invoice_value"],
                -x["days_overdue"],
                x["next_billing_date"],
            )
        )
        return ranked

    @classmethod
    def _build_weekly_roadmap(
        cls, due_ranked: List[Dict[str, Any]], weekly_capacity: int, as_of: date
    ) -> List[Dict[str, Any]]:
        roadmap = []
        if not due_ranked:
            return roadmap
        week_start = as_of - timedelta(days=as_of.weekday())
        idx = 0
        week = 1
        while idx < len(due_ranked):
            chunk = due_ranked[idx : idx + weekly_capacity]
            ws = week_start + timedelta(days=(week - 1) * 7)
            we = ws + timedelta(days=6)
            roadmap.append(
                {
                    "week_index": week,
                    "week_start": ws.isoformat(),
                    "week_end": we.isoformat(),
                    "planned_billings": len(chunk),
                    "planned_estimated_value": round(
                        sum(x["estimated_invoice_value"] for x in chunk), 2
                    ),
                    # Key must not be "items" — Jinja `wk.items` binds dict.items (method),
                    # which raises TypeError: 'builtin_function_or_method' object is not iterable.
                    "planned_clients": [
                        {
                            "agreement_id": x["agreement_id"],
                            "client_id": x["client_id"],
                            "client_name": x["client_name"],
                            "estimated_invoice_value": x["estimated_invoice_value"],
                            "days_overdue": x["days_overdue"],
                            "selection_reason": x["selection_reason"],
                        }
                        for x in chunk
                    ],
                }
            )
            idx += weekly_capacity
            week += 1
        return roadmap

    @classmethod
    def _build_upcoming(
        cls, opportunities: List[Dict[str, Any]], as_of: date, horizon_days: int
    ) -> List[Dict[str, Any]]:
        end = as_of + timedelta(days=horizon_days)
        out = []
        for o in opportunities:
            nb = cls._parse_iso_date(o["next_billing_date"])
            if as_of <= nb <= end:
                out.append(
                    {
                        "agreement_id": o["agreement_id"],
                        "client_id": o["client_id"],
                        "client_name": o["client_name"],
                        "next_billing_date": o["next_billing_date"],
                        "estimated_invoice_value": o["estimated_invoice_value"],
                        "has_hard_block": o["has_hard_block"],
                    }
                )
        out.sort(
            key=lambda x: (
                x["next_billing_date"],
                x["has_hard_block"],
                -x["estimated_invoice_value"],
            )
        )
        return out

    @classmethod
    def _flags_from_calc_error(cls, err: str) -> List[BillingInputFlag]:
        e = (err or "").lower()
        out: List[BillingInputFlag] = []
        if "no billing rates found" in e:
            out.append(
                BillingInputFlag("MISSING_BILLING_RATES", "hard", "No active billing rates.")
            )
        elif "no client found" in e:
            out.append(BillingInputFlag("MISSING_CLIENT_LINK", "hard", "No client for agreement."))
        elif "agreement" in e and "not found" in e:
            out.append(BillingInputFlag("MISSING_AGREEMENT", "hard", "Agreement not found."))
        else:
            out.append(
                BillingInputFlag(
                    "CALCULATION_ERROR",
                    "hard",
                    f"Billing calculation failed: {err}",
                )
            )
        return out

    @classmethod
    def _active_schedule(cls, agreement: Agreement) -> Optional[BillingSchedule]:
        schedules = [s for s in (agreement.billing_schedules or []) if s.is_active]
        if not schedules:
            return None
        schedules.sort(
            key=lambda s: (s.updated_at or datetime.min, s.created_at or datetime.min),
            reverse=True,
        )
        return schedules[0]

    @classmethod
    def _extract_frequency(cls, agreement: Agreement) -> Tuple[str, bool]:
        for var in agreement.variables or []:
            if (var.variable_name or "").strip().lower() == "billing_frequency":
                v = (var.variable_value or "").strip().lower()
                if v in cls.FREQ_MONTHS:
                    return v, False
        return "yearly", True

    @classmethod
    def _latest_invoice_date(
        cls, agreement_id: int, client_id: Optional[int] = None
    ) -> Optional[date]:
        """Latest non-cancelled invoice date for this agreement, including imported client invoices."""
        q = Invoice.query.filter(Invoice.status != "cancelled")
        if client_id:
            q = q.filter(Invoice.client_id == client_id).filter(
                or_(Invoice.agreement_id == agreement_id, Invoice.agreement_id.is_(None))
            )
        else:
            q = q.filter(Invoice.agreement_id == agreement_id)
        row = q.order_by(Invoice.invoice_date.desc()).with_entities(Invoice.invoice_date).first()
        return cls._coerce_date(row[0]) if row else None

    @staticmethod
    def _coerce_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return None

    @classmethod
    def _effective_last_billed(
        cls,
        schedule_last: Optional[date],
        latest_invoice: Optional[date],
    ) -> Optional[date]:
        dates = [d for d in (cls._coerce_date(schedule_last), cls._coerce_date(latest_invoice)) if d]
        return max(dates) if dates else None

    @classmethod
    def _add_billing_periods(cls, start: date, frequency: str, periods: int = 1) -> date:
        """Advance by calendar months (same cadence as invoice schedule updates)."""
        months = cls.FREQ_MONTHS.get(frequency, 12) * max(1, periods)
        month = start.month + months
        year = start.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(start.day, monthrange(year, month)[1])
        return date(year, month, day)

    @classmethod
    def _resolve_next_billing_date(
        cls,
        schedule: Optional[BillingSchedule],
        last_billed: Optional[date],
        frequency: str,
        agreement: Agreement,
        as_of: date,
    ) -> date:
        stored_next = cls._coerce_date(schedule.next_billing_date) if schedule else None
        last_billed = cls._coerce_date(last_billed)
        if last_billed:
            computed = cls._add_billing_periods(last_billed, frequency)
            # Trust a stored next date only when it is still in the future vs last billed.
            if stored_next and stored_next > last_billed:
                return stored_next
            return computed
        if stored_next:
            return stored_next
        start = None
        if schedule and schedule.billing_start_date:
            start = cls._coerce_date(schedule.billing_start_date)
        if start is None and agreement.signed_date:
            start = cls._coerce_date(agreement.signed_date)
        if start is None and agreement.created_at:
            start = cls._coerce_date(agreement.created_at)
        if start:
            return cls._add_billing_periods(start, frequency)
        return as_of

    @staticmethod
    def _parse_iso_date(s: str) -> date:
        return date.fromisoformat(s)
