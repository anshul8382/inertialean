#!/usr/bin/env python3
"""Audit live notification-centre output against FINDING_PROCESSING_GUIDELINES."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import create_app
from models import OpsTask, ServiceTicket, User
from services import notification_centre_service as ncs
from services.db_cutover import finding_notification_enabled, peer_messages_enabled


EXPECTED_SIGNALS = {
    "A1",
    "A2",
    "A3",
    "A5",
    "A6",
    "C1",
    "C2",
    "D1",
    "D2",
    "D3",
    "E1",
    "F1",
    "T1",
    "ATT1",
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    "G6",
    "G7",
    "H2",
    "J1",
    "J2",
    "J5",
    "J7",
    "K2",
    "K3",
    "I1",
    "P1",
    "B1",
}


def main() -> None:
    app = create_app()
    now = datetime.utcnow()
    report: dict = {"generated_at_utc": now.isoformat() + "Z"}

    with app.app_context():
        report["gates"] = {
            "finding_notification_enabled": finding_notification_enabled(),
            "peer_messages_enabled": peer_messages_enabled(),
        }
        samples = []
        for uname in ["anshul", "sharveen", "Pradeep", "swati"]:
            u = User.query.filter_by(username=uname).first()
            if u:
                samples.append(u)
        if not samples:
            samples = User.query.filter_by(is_active=True).limit(3).all()

        u0 = samples[0]
        live = ncs.collect_live_items(u0, now)
        counts = Counter(i.signal_id for i in live)
        report["sample_user"] = u0.username
        report["total_live"] = len(live)
        report["signal_counts"] = dict(counts)
        report["implemented"] = sorted(counts.keys())
        report["missing_expected"] = sorted(EXPECTED_SIGNALS - set(counts.keys()))

        per_user = []
        ignore_violations = []
        ignore_should_allow = []
        for u in samples:
            payload = ncs.list_for_user(u, view="open")
            items = payload.get("items") or []
            sc = Counter(i["signal_id"] for i in items)
            for i in items:
                sid = i["signal_id"]
                if sid in ncs.IGNORE_NOT_ALLOWED and i.get("ignore_allowed"):
                    ignore_violations.append(
                        {"user": u.username, "signal": sid, "id": i["entity_id"]}
                    )
                if sid in ("D1", "C2") and not i.get("ignore_allowed"):
                    ignore_should_allow.append(
                        {
                            "user": u.username,
                            "signal": sid,
                            "entity_id": i["entity_id"],
                        }
                    )
            per_user.append(
                {
                    "username": u.username,
                    "role": getattr(u, "role", None),
                    "is_admin": bool(getattr(u, "is_admin", False)),
                    "open_count": payload.get("count"),
                    "urgent_count": payload.get("urgent_count"),
                    "message_count": payload.get("message_count"),
                    "by_signal": dict(sc),
                }
            )
        report["per_user"] = per_user
        report["ignore_violations"] = ignore_violations[:30]
        report["ignore_should_allow_but_blocked"] = ignore_should_allow[:30]

        from models import Workflow
        from sqlalchemy.orm import joinedload

        wfs = (
            Workflow.query.options(joinedload(Workflow.monthly_investment))
            .filter(
                Workflow.is_archived == False,  # noqa: E712
                Workflow.current_stage.in_(["RECOS", "NOTIFY", "UPDATE", "FUNDS", "EXEC"]),
            )
            .all()
        )
        report["workflow_stage_counts"] = dict(Counter(w.current_stage for w in wfs))

        def audit_sla(items, threshold, field="age_hours"):
            rows = []
            for i in items[:25]:
                age = getattr(i, field)
                expect = age >= threshold
                rows.append(
                    {
                        "id": i.entity_id,
                        "age": round(age, 1),
                        "urgent": i.urgent,
                        "severity": i.severity,
                        "age_label": i.age_label,
                        "expect_urgent": expect,
                        "match": i.urgent == expect,
                    }
                )
            return rows

        a2 = [i for i in live if i.signal_id == "A2"]
        a3 = [i for i in live if i.signal_id == "A3"]
        g6 = [i for i in live if i.signal_id == "G6"]
        g5 = [i for i in live if i.signal_id == "G5"]
        g7 = [i for i in live if i.signal_id == "G7"]
        c2 = [i for i in live if i.signal_id == "C2"]
        f1 = [i for i in live if i.signal_id == "F1"]
        t1 = [i for i in live if i.signal_id == "T1"]

        report["a2_audit"] = audit_sla(a2, 48)
        report["a3_audit"] = [
            {
                "id": i.entity_id,
                "age": round(i.age_hours, 2),
                "urgent": i.urgent,
                "age_label": i.age_label,
                "severity": i.severity,
                "expect_urgent_ge_1h_wall": i.age_hours >= 1,
                "match": i.urgent == (i.age_hours >= 1),
            }
            for i in a3[:25]
        ]
        report["g7_audit"] = audit_sla(g7, 48)
        report["g6_audit"] = {
            "count": len(g6),
            "all_urgent": all(i.urgent for i in g6) if g6 else None,
            "any_ignore_allowed": any(i.ignore_allowed for i in g6),
            "guideline_ignore": "one_off (should allow)",
            "samples": [
                {
                    "title": i.title,
                    "members": i.member_count,
                    "details": len(i.detail_lines or []),
                    "urgent": i.urgent,
                    "ignore_allowed": i.ignore_allowed,
                }
                for i in g6[:8]
            ],
        }
        g5_keys = set()
        for i in g5[:40]:
            g5_keys |= set((i.facts or {}).keys())
        report["g5_audit"] = {
            "count": len(g5),
            "fact_keys": sorted(g5_keys),
            "has_ladder_rung": any(
                (i.facts or {}).get("ladder_rung") or (i.facts or {}).get("rung")
                for i in g5[:80]
            ),
            "sample_titles": [i.title for i in g5[:6]],
        }
        report["c2_audit"] = {
            "count": len(c2),
            "done_labels": [(i.facts or {}).get("done_label") for i in c2[:15]],
            "ignore_allowed": [i.ignore_allowed for i in c2[:15]],
        }
        report["f1_audit"] = {
            "live": len(f1),
            "db_open": ServiceTicket.query.filter(
                ServiceTicket.status.in_(["open", "in_progress"])
            ).count(),
            "past_urgent": sum(1 for i in f1 if i.urgent),
            "samples": [
                {
                    "title": i.title,
                    "age_label": i.age_label,
                    "urgent": i.urgent,
                    "severity": i.severity,
                }
                for i in f1[:8]
            ],
        }
        report["t1_audit"] = {
            "live": len(t1),
            "db_assigned_pending": OpsTask.query.filter(
                OpsTask.assigned_to == u0.id,
                OpsTask.status.in_(["pending", "in_progress"]),
            ).count(),
            "samples": [{"title": i.title, "urgent": i.urgent} for i in t1[:8]],
        }
        t1_by_user = {}
        for u in samples:
            items = [
                i for i in ncs.collect_live_items(u, now) if i.signal_id == "T1"
            ]
            t1_by_user[u.username] = [i.entity_id for i in items]
        report["t1_by_user"] = t1_by_user

        report["att1"] = {
            "ist_today": str(ncs._ist_today(now)),
            "now_count": len(ncs._from_attendance_month_end(u0, now)),
            "forced_month_end": len(
                ncs._from_attendance_month_end(u0, datetime(2026, 9, 30, 10, 0, 0))
            ),
        }

        from models.finding_notification_decision import FindingNotificationDecision

        try:
            report["decisions"] = dict(
                Counter(r.decision for r in FindingNotificationDecision.query.all())
            )
        except Exception as exc:
            report["decisions"] = {"error": str(exc)}

        # Code-level gaps (known from guidelines MVP notes)
        report["known_mvp_gaps"] = [
            {
                "id": "stage_entered_at",
                "signals": ["A3", "A5"],
                "status": "partial",
                "note": "Uses WorkflowAction/updated_at best-effort; no dedicated entered_at column",
            },
            {
                "id": "g5_ladder",
                "signals": ["G5"],
                "status": "missing",
                "note": "Silence nudge present; WhatsApp draft ladder + rung schema not shipped",
            },
            {
                "id": "g6_at_entry",
                "signals": ["G6"],
                "status": "partial",
                "note": "Centre notifies + groups; at-point-of-entry two-button UI not in entry forms",
            },
            {
                "id": "c2_close_gate",
                "signals": ["C2", "D3"],
                "status": "missing",
                "note": "Close-requires-notes not enforced on review save",
            },
            {
                "id": "a6_rollover",
                "signals": ["A6"],
                "status": "missing",
                "note": "Blocked on rollover trigger — not in live feed",
            },
            {
                "id": "digests",
                "signals": ["radar"],
                "status": "missing",
                "note": "Manager/admin digest email wiring not shipped",
            },
            {
                "id": "task_auto_close_blockers",
                "signals": ["rule2", "rule7"],
                "status": "open_blocker",
                "note": "close_completed_ops_tasks >7d and COMPLETED short-circuit still in code",
            },
        ]

        # Compliance scorecard
        checks = []

        def add(name, ok, detail):
            checks.append({"check": name, "ok": ok, "detail": detail})

        add(
            "Schema gates on",
            report["gates"]["finding_notification_enabled"]
            and report["gates"]["peer_messages_enabled"],
            str(report["gates"]),
        )
        add(
            "A4 absent (merged→G5)",
            counts.get("A4", 0) == 0,
            f"A4={counts.get('A4', 0)} G5={counts.get('G5', 0)}",
        )
        add(
            "H1 absent (fold→A*)",
            counts.get("H1", 0) == 0,
            f"H1={counts.get('H1', 0)}",
        )
        add(
            "A2 urgent ≥48 working hrs",
            all(r["match"] for r in report["a2_audit"]) if report["a2_audit"] else True,
            f"checked {len(report['a2_audit'])} rows",
        )
        add(
            "A3 urgent ≥1h wall",
            all(r["match"] for r in report["a3_audit"]) if report["a3_audit"] else True,
            f"checked {len(report['a3_audit'])}; labels={Counter(i.age_label for i in a3)}",
        )
        add(
            "G6 all urgent",
            report["g6_audit"]["all_urgent"] in (True, None),
            str(report["g6_audit"]["all_urgent"]),
        )
        add(
            "G6 ignore one_off allowed",
            report["g6_audit"]["any_ignore_allowed"] in (True, None)
            or report["g6_audit"]["count"] == 0,
            f"any_ignore_allowed={report['g6_audit']['any_ignore_allowed']} (guideline: one_off)",
        )
        add(
            "G5 ladder NOT yet shipped",
            not report["g5_audit"]["has_ladder_rung"],
            "expected gap until ladder schema",
        )
        add(
            "C2 Done=Planned",
            all(x == "Planned" for x in report["c2_audit"]["done_labels"])
            if report["c2_audit"]["done_labels"]
            else True,
            str(report["c2_audit"]["done_labels"][:5]),
        )
        add(
            "F1 covers open tickets for assignee",
            report["f1_audit"]["live"] >= 1 or report["f1_audit"]["db_open"] == 0,
            f"live={report['f1_audit']['live']} db_open={report['f1_audit']['db_open']}",
        )
        add(
            "T1 matches assigned pending tasks",
            report["t1_audit"]["live"] == report["t1_audit"]["db_assigned_pending"],
            str(report["t1_audit"]),
        )
        add(
            "No ignore_allowed on IGNORE_NOT_ALLOWED set",
            len(ignore_violations) == 0,
            f"violations={len(ignore_violations)}",
        )
        add(
            "C2 ignore should be allowed (persistent)",
            not any(x["signal"] == "C2" for x in ignore_should_allow),
            f"blocked rows={ [x for x in ignore_should_allow if x['signal']=='C2'][:5] }",
        )
        add(
            "ATT1 dormant mid-month",
            report["att1"]["now_count"] == 0
            or ncs._ist_today(now).day
            >= ncs._month_end_date(
                ncs._ist_today(now).year, ncs._ist_today(now).month
            ).day
            or ncs._ist_today(now).day <= 3,
            str(report["att1"]),
        )
        add(
            "ATT1 fires on forced month-end",
            report["att1"]["forced_month_end"] >= 1,
            str(report["att1"]["forced_month_end"]),
        )
        # E1 / J / D1 D2 A6 B1 presence
        for sid in ["E1", "D1", "D2", "A6", "B1", "J1", "J2", "P1"]:
            add(
                f"{sid} in live feed",
                counts.get(sid, 0) > 0,
                f"count={counts.get(sid, 0)} (0 may mean no data OR not implemented)",
            )

        report["checks"] = checks
        report["pass"] = sum(1 for c in checks if c["ok"])
        report["fail"] = sum(1 for c in checks if not c["ok"])

    out = Path("/tmp/nc_guidelines_audit.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(
        {
            "sample_user": report["sample_user"],
            "total_live": report["total_live"],
            "pass": report["pass"],
            "fail": report["fail"],
            "signal_counts": report["signal_counts"],
            "missing_expected": report["missing_expected"],
            "checks_failed": [c for c in report["checks"] if not c["ok"]],
            "g5_audit": report["g5_audit"],
            "g6_audit": report["g6_audit"],
            "c2_audit": report["c2_audit"],
            "a3_audit_sample": report["a3_audit"][:5],
            "a2_audit_sample": report["a2_audit"][:5],
            "att1": report["att1"],
            "per_user": report["per_user"],
            "known_mvp_gaps": report["known_mvp_gaps"],
        },
        indent=2,
        default=str,
    ))
    print("WROTE", out)


if __name__ == "__main__":
    main()
