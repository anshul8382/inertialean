"""
Agreement overview service
--------------------------
Produces a client-scoped view of agreement readiness for billing:
- Which accessible clients have agreements on file
- Key agreement dates/status
- Billing frequency variable
- Active billing schedule / billing rate presence
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

MONTH_NUM_TO_NAME = {
    "1": "January",
    "2": "February",
    "3": "March",
    "4": "April",
    "5": "May",
    "6": "June",
    "7": "July",
    "8": "August",
    "9": "September",
    "10": "October",
    "11": "November",
    "12": "December",
}

VALUATION_RULE_LABELS = {
    "prepaid": "Pre-paid (AUA at end of prior period)",
    "postpaid": "Post-paid (AUA at end of current period)",
    "day_before_period_start": "Pre-paid (legacy)",
}

from sqlalchemy.orm import joinedload

from models import Agreement, AssetClass, BillingRateStructure, BillingSchedule, Client, Invoice, Lead
from services.agreement_pdf_paths import agreement_pdf_abs_path


@dataclass(frozen=True)
class AgreementOverviewRow:
    client_id: int
    client_name: str
    agreement_id: Optional[int]
    agreement_status: Optional[str]
    signed_date: Optional[datetime]
    created_at: Optional[datetime]
    advisory_model: Optional[str]
    billing_frequency: Optional[str]
    period_start_month: Optional[str]
    valuation_date_rule: Optional[str]
    fixed_annual_fee: Optional[str]
    fixed_fee_escalation_pct: Optional[str]
    has_active_billing_schedule: bool
    has_active_billing_rates: bool
    billing_start_date: Optional[datetime]
    last_billing_date: Optional[datetime]
    next_billing_date: Optional[datetime]
    billing_cycle_number: Optional[int]
    period_start_month_display: Optional[str] = None
    valuation_date_display: Optional[str] = None
    billing_rates_by_asset: List[Dict[str, Any]] = field(default_factory=list)
    flags: List[Dict[str, str]] = field(default_factory=list)


class AgreementOverviewService:
    ACTIVE_AGREEMENT_STATUSES = {"signed", "active", "completed"}

    @classmethod
    def build_overview(
        cls,
        accessible_clients: List[Client],
        missing_only: bool = False,
        q: Optional[str] = None,
    ) -> Dict[str, Any]:
        allowed_ids = [c.id for c in accessible_clients]
        q_norm = (q or "").strip().lower()

        clients = accessible_clients
        if q_norm:
            clients = [c for c in clients if q_norm in (c.name or "").lower()]

        agreements = (
            Agreement.query.options(
                joinedload(Agreement.lead).joinedload(Lead.client),
                joinedload(Agreement.variables),
                joinedload(Agreement.billing_schedules),
                joinedload(Agreement.billing_rates).joinedload(
                    BillingRateStructure.asset_class
                ),
            )
            .filter(Agreement.lead.has(Lead.client_id.in_(allowed_ids)))
            .all()
        )

        by_client: Dict[int, List[Agreement]] = {}
        for ag in agreements:
            cid = ag.lead.client_id if ag.lead else None
            if cid is None:
                continue
            by_client.setdefault(cid, []).append(ag)

        rows: List[AgreementOverviewRow] = []
        for c in clients:
            ags = by_client.get(c.id) or []
            if not ags:
                row = AgreementOverviewRow(
                    client_id=c.id,
                    client_name=c.name,
                    agreement_id=None,
                    agreement_status=None,
                    signed_date=None,
                    created_at=None,
                    advisory_model=None,
                    billing_frequency=None,
                    period_start_month=None,
                    valuation_date_rule=None,
                    fixed_annual_fee=None,
                    fixed_fee_escalation_pct=None,
                    has_active_billing_schedule=False,
                    has_active_billing_rates=False,
                    billing_start_date=None,
                    last_billing_date=None,
                    next_billing_date=None,
                    billing_cycle_number=None,
                    flags=[{"code": "MISSING_AGREEMENT", "severity": "hard", "message": "No agreement on file."}],
                )
                if not missing_only:
                    rows.append(row)
                else:
                    rows.append(row)
                continue

            selected = cls._pick_primary_agreement(ags)
            flags: List[Dict[str, str]] = []

            status = (selected.status or "").strip().lower() if selected.status else None
            if not status or status not in cls.ACTIVE_AGREEMENT_STATUSES:
                flags.append(
                    {
                        "code": "AGREEMENT_NOT_ACTIVE",
                        "severity": "soft",
                        "message": f"Agreement status is {selected.status or 'unknown'} (billing typically expects signed/active).",
                    }
                )

            billing_frequency = cls._extract_variable(selected, "billing_frequency")
            advisory_model = cls._extract_variable(selected, "advisory_model")
            period_start_month = cls._extract_variable(selected, "period_start_month")
            valuation_date_rule = cls._extract_variable(selected, "valuation_date_rule")
            fixed_annual_fee = cls._extract_variable(selected, "fixed_annual_fee")
            fixed_fee_escalation_pct = cls._extract_variable(selected, "fixed_fee_escalation_pct")
            if not billing_frequency:
                flags.append(
                    {
                        "code": "MISSING_BILLING_FREQUENCY",
                        "severity": "soft",
                        "message": "billing_frequency is missing (defaults to yearly in calculation).",
                    }
                )

            active_sched = next((s for s in (selected.billing_schedules or []) if getattr(s, "is_active", False)), None)
            has_sched = bool(active_sched)
            if not has_sched:
                flags.append(
                    {
                        "code": "MISSING_BILLING_SCHEDULE",
                        "severity": "hard",
                        "message": "No active billing schedule found.",
                    }
                )

            has_rates = any(getattr(r, "is_active", False) for r in (selected.billing_rates or []))
            if not has_rates:
                flags.append(
                    {
                        "code": "MISSING_BILLING_RATES",
                        "severity": "hard",
                        "message": "No active billing rates found (BillingRateStructure).",
                    }
                )

            row = AgreementOverviewRow(
                client_id=c.id,
                client_name=c.name,
                agreement_id=selected.id,
                agreement_status=selected.status,
                signed_date=selected.signed_date,
                created_at=selected.created_at,
                advisory_model=advisory_model,
                billing_frequency=billing_frequency,
                period_start_month=period_start_month,
                valuation_date_rule=valuation_date_rule,
                fixed_annual_fee=fixed_annual_fee,
                fixed_fee_escalation_pct=fixed_fee_escalation_pct,
                has_active_billing_schedule=has_sched,
                has_active_billing_rates=has_rates,
                billing_start_date=getattr(active_sched, "billing_start_date", None),
                last_billing_date=getattr(active_sched, "last_billing_date", None),
                next_billing_date=getattr(active_sched, "next_billing_date", None),
                billing_cycle_number=getattr(active_sched, "billing_cycle_number", None),
                period_start_month_display=cls.format_period_start_month(period_start_month),
                valuation_date_display=cls.format_valuation_rule(valuation_date_rule),
                billing_rates_by_asset=cls.build_billing_rates_by_asset(
                    selected, advisory_model, fixed_annual_fee, fixed_fee_escalation_pct
                ),
                flags=flags,
            )

            if missing_only and not cls._row_is_missing(row):
                continue
            rows.append(row)

        rows.sort(key=lambda r: ((r.client_name or "").lower(), r.client_id))

        missing_count = sum(1 for r in rows if cls._row_is_missing(r))
        return {
            "rows": rows,
            "summary": {
                "clients_scanned": len(clients),
                "rows_returned": len(rows),
                "missing_or_incomplete": missing_count,
            },
        }

    @staticmethod
    def format_period_start_month(month_val: Optional[str]) -> Optional[str]:
        if not month_val or not str(month_val).strip():
            return None
        key = str(month_val).strip()
        if key in MONTH_NUM_TO_NAME:
            return MONTH_NUM_TO_NAME[key]
        try:
            m = int(key)
            if 1 <= m <= 12:
                return MONTH_NUM_TO_NAME[str(m)]
        except (TypeError, ValueError):
            pass
        return key

    @staticmethod
    def format_valuation_rule(rule: Optional[str]) -> Optional[str]:
        if not rule or not str(rule).strip():
            return None
        key = str(rule).strip().lower()
        return VALUATION_RULE_LABELS.get(key, rule)

    @classmethod
    def build_billing_rates_by_asset(
        cls,
        agreement: Agreement,
        advisory_model: Optional[str],
        fixed_annual_fee: Optional[str],
        fixed_fee_escalation_pct: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Group active BillingRateStructure rows by asset class for display."""
        model = (advisory_model or "").strip().lower()
        if model == "fixed_fee" or (fixed_annual_fee and not model):
            rows = []
            if fixed_annual_fee:
                rows.append(
                    {
                        "asset_class": "Fixed fee (all assets)",
                        "slabs": [
                            {
                                "label": "Annual fee",
                                "rate_pct": None,
                                "amount_inr": fixed_annual_fee,
                                "escalation_pct": fixed_fee_escalation_pct,
                            }
                        ],
                    }
                )
            return rows

        from collections import defaultdict

        by_ac: Dict[str, List[BillingRateStructure]] = defaultdict(list)
        for r in agreement.billing_rates or []:
            if not getattr(r, "is_active", True):
                continue
            ac_name = (
                r.asset_class.name
                if r.asset_class
                else f"Asset class #{r.asset_class_id}"
            )
            by_ac[ac_name].append(r)

        result: List[Dict[str, Any]] = []
        for ac_name in sorted(by_ac.keys()):
            slabs_raw = sorted(by_ac[ac_name], key=lambda x: float(x.min_amount or 0))
            slabs = []
            for s in slabs_raw:
                max_amt = float(s.max_amount) if s.max_amount is not None else None
                max_fee = float(s.max_fee) if s.max_fee is not None else None
                slabs.append(
                    {
                        "min_amount": float(s.min_amount or 0),
                        "max_amount": max_amt,
                        "rate_pct": round(float(s.rate_percentage or 0) * 100, 4),
                        "min_fee": float(s.min_fee or 0),
                        "max_fee": max_fee,
                        "is_tiered": len(slabs_raw) > 1,
                    }
                )
            result.append({"asset_class": ac_name, "slabs": slabs})
        return result

    @staticmethod
    def _extract_variable(agreement: Agreement, name: str) -> Optional[str]:
        target = (name or "").strip().lower()
        for var in agreement.variables or []:
            if (var.variable_name or "").strip().lower() == target:
                v = (var.variable_value or "").strip()
                return v or None
        return None

    @staticmethod
    def _pick_primary_agreement(agreements: List[Agreement]) -> Agreement:
        # Prefer most recently signed; fallback to most recently created.
        def key(a: Agreement):
            sd = a.signed_date or datetime.min
            ca = a.created_at or datetime.min
            return (sd, ca, a.id or 0)

        return sorted(agreements, key=key, reverse=True)[0]

    @staticmethod
    def _row_is_missing(row: AgreementOverviewRow) -> bool:
        if row.agreement_id is None:
            return True
        return any(f.get("severity") == "hard" for f in (row.flags or []))

    @classmethod
    def get_client_card_context(cls, client_id: int) -> Dict[str, Any]:
        """
        Load agreement + billing context for the client details Billing & Agreement card.
        """
        empty = {
            "active_agreement": None,
            "agreement_variables": {},
            "billing_schedule": None,
            "billing_rates": [],
            "recent_invoices": [],
            "pdf_downloadable": False,
        }
        lead = Lead.query.filter_by(client_id=client_id).first()
        if not lead:
            return empty

        agreements = (
            Agreement.query.options(
                joinedload(Agreement.billing_schedules),
                joinedload(Agreement.billing_rates).joinedload(BillingRateStructure.asset_class),
                joinedload(Agreement.variables),
            )
            .filter_by(lead_id=lead.id)
            .all()
        )
        if not agreements:
            return empty

        preferred = [
            a for a in agreements
            if (a.status or "").strip().lower() in cls.ACTIVE_AGREEMENT_STATUSES
        ]
        active_agreement = cls._pick_primary_agreement(preferred or agreements)

        config: Dict[str, Any] = {}
        if active_agreement.agreement_data:
            try:
                config = json.loads(active_agreement.agreement_data)
            except (json.JSONDecodeError, TypeError):
                config = {}

        advisory_model = config.get("advisory_model") or cls._extract_variable(
            active_agreement, "advisory_model"
        )
        special_note = config.get("special_note") or cls._extract_variable(
            active_agreement, "special_note"
        )
        billing_frequency = cls._extract_variable(active_agreement, "billing_frequency")

        asset_names: List[str] = []
        raw_asset_types = config.get("asset_types") or []
        if raw_asset_types:
            id_list = [int(x) for x in raw_asset_types if str(x).isdigit()]
            if id_list:
                for ac in AssetClass.query.filter(AssetClass.id.in_(id_list)).all():
                    asset_names.append(ac.name)
            else:
                asset_names = [str(x) for x in raw_asset_types]

        agreement_variables = {
            "advisory_model": advisory_model,
            "asset_types": asset_names,
            "special_note": special_note,
            "billing_frequency": billing_frequency,
        }

        billing_schedule = next(
            (s for s in (active_agreement.billing_schedules or []) if getattr(s, "is_active", False)),
            None,
        )
        billing_rates = [
            r for r in (active_agreement.billing_rates or []) if getattr(r, "is_active", False)
        ]

        recent_invoices = (
            Invoice.query.filter_by(client_id=client_id)
            .order_by(Invoice.invoice_date.desc())
            .limit(5)
            .all()
        )

        return {
            "active_agreement": active_agreement,
            "agreement_variables": agreement_variables,
            "billing_schedule": billing_schedule,
            "billing_rates": billing_rates,
            "recent_invoices": recent_invoices,
            "pdf_downloadable": bool(
                agreement_pdf_abs_path(active_agreement.generated_pdf_path)
            ),
        }

