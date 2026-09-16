"""
Billing Manager Service
-----------------------
On-request planner to prioritize due billings, maximize billed value,
and produce daily/weekly/monthly catch-up plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from calendar import monthrange

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from billing_calculation_service import BillingCalculationService
from models import Agreement, BillingSchedule, Invoice, Lead


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
        capacity = max(1, min(int(weekly_capacity or 5), 5))
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

        opportunities: List[Dict[str, Any]] = []
        missing_inputs: List[Dict[str, Any]] = []
        for ag in agreements:
            opp = cls._build_opportunity(ag, as_of)
            if enforce_access and (opp["client_id"] not in allowed_clients):
                continue
            opportunities.append(opp)
            if opp["flags"]:
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
            if o["is_due"] and not o["has_hard_block"] and o["estimated_invoice_value"] > 0
        ]
        due_ranked = cls._rank_due(due_candidates, value_weight, overdue_weight)

        daily_plan = due_ranked[:capacity]
        weekly_plan = due_ranked[:capacity]
        roadmap = cls._build_weekly_roadmap(due_ranked, capacity, as_of)
        upcoming = cls._build_upcoming(opportunities, as_of, horizon_days=30)

        total_due_value = round(sum(x["estimated_invoice_value"] for x in due_ranked), 2)
        due_count = len(due_ranked)
        weeks_to_catch_up = (due_count + capacity - 1) // capacity if due_count else 0

        catch_up_week = None
        if roadmap:
            catch_up_week = roadmap[-1]

        return {
            "as_of_date": as_of.isoformat(),
            "parameters": {
                "weekly_capacity": capacity,
                "value_weight": value_weight,
                "overdue_weight": overdue_weight,
            },
            "summary": {
                "active_agreements_scanned": len(agreements),
                "due_billings": due_count,
                "total_due_estimated_value": total_due_value,
                "weeks_to_catch_up": weeks_to_catch_up,
                "catch_up_by_week": catch_up_week["week_index"] if catch_up_week else None,
                "catch_up_by_week_end": catch_up_week["week_end"] if catch_up_week else None,
            },
            "daily_plan": daily_plan,
            "weekly_plan": weekly_plan,
            "monthly_projection": {
                "upcoming_30_days": upcoming,
                "roadmap_by_week": roadmap,
            },
            "missing_inputs": missing_inputs,
        }

    @classmethod
    def _build_opportunity(cls, ag: Agreement, as_of: date) -> Dict[str, Any]:
        flags: List[BillingInputFlag] = []
        lead = ag.lead
        client = lead.client if lead and getattr(lead, "client", None) else None
        client_name = client.name if client else "Unknown"
        client_id = client.id if client else None

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

        latest_invoice_date = cls._latest_invoice_date(ag.id, client_id=client_id)
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

        days_overdue = max((as_of - next_billing).days, 0)
        is_due = next_billing <= as_of

        hard_block = any(f.severity == "hard" for f in flags)
        return {
            "agreement_id": ag.id,
            "client_id": client_id,
            "client_name": client_name,
            "last_billed_date": last_billed.isoformat() if last_billed else None,
            "next_billing_date": next_billing.isoformat(),
            "billing_frequency": frequency,
            "is_due": is_due,
            "days_overdue": days_overdue,
            "estimated_invoice_value": round(estimate, 2),
            "estimate_error": estimate_error,
            "excluded_assets": excluded_assets[:20],
            "has_hard_block": hard_block,
            "flags": [
                {"code": f.code, "severity": f.severity, "message": f.message}
                for f in flags
            ],
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
        max_value = max(x["estimated_invoice_value"] for x in due_items) or 1.0
        max_days = max(x["days_overdue"] for x in due_items) or 1
        denom = value_weight + overdue_weight
        ranked = []
        for item in due_items:
            value_norm = item["estimated_invoice_value"] / max_value
            overdue_norm = item["days_overdue"] / max_days
            score = ((value_weight * value_norm) + (overdue_weight * overdue_norm)) / denom
            r = dict(item)
            r["priority_score"] = round(score, 4)
            if overdue_norm > 0.75 and value_norm < 0.25:
                r["selection_reason"] = "severely_overdue"
            elif value_norm >= 0.75:
                r["selection_reason"] = "high_value_due"
            else:
                r["selection_reason"] = "balanced_priority"
            ranked.append(r)
        ranked.sort(
            key=lambda x: (
                -x["priority_score"],
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
