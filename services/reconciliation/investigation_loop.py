"""
Multi-pass holdings investigation — Python owns cause codes.

Runs iterative probes until ranked explanations are scored (does not stop
after the first portfolio pattern). Narrative is template-only (no LLM).
"""
from __future__ import annotations

from datetime import date
from statistics import median
from typing import Any, Dict, List, Optional, Tuple


def _pass(
    n: int,
    name: str,
    *,
    findings: List[str],
    candidate_cause: Optional[str] = None,
    eliminated: Optional[List[str]] = None,
    stop: bool = False,
) -> Dict[str, Any]:
    return {
        "pass": n,
        "name": name,
        "findings": findings,
        "candidate_cause": candidate_cause,
        "eliminated": eliminated or [],
        "stop": stop,
    }


def _sym(b: Dict[str, Any]) -> str:
    return (b.get("symbol") or str(b.get("security_id") or "?")).upper()


def _gap_decomposition(breaks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify breaks by qty ratio / story — no DB."""
    total_abs = sum(abs(float(b.get("delta") or 0)) for b in breaks) or 1.0
    ratios: List[float] = []
    missing_stmt: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []  # 0 < stmt/sys < 0.95
    near_full: List[Dict[str, Any]] = []  # ratio >= 0.95 but still break
    reco_explained: List[str] = []
    missed_sell_no_email: List[str] = []
    email_sell_explains: List[str] = []
    quiet_no_trades: List[str] = []
    contrib: List[Tuple[str, float]] = []

    for b in breaks:
        sym = _sym(b)
        sq = float(b.get("statement_qty") or 0)
        yq = float(b.get("system_qty") or 0)
        delta = float(b.get("delta") or 0)
        abs_d = abs(delta)
        contrib.append((sym, abs_d))
        d = b.get("diagnosis") or {}
        if_applied = d.get("if_reco_applied") or {}
        story = d.get("trade_story") or {}
        hints = (d.get("emails") or {}).get("symbol_hints") or []
        sell_hints = [
            h
            for h in hints
            if (h.get("action") or "").upper() == "SELL" and h.get("quantity") is not None
        ]

        if if_applied.get("explains_gap"):
            reco_explained.append(sym)

        if yq > 0.001:
            r = sq / yq
            ratios.append(r)
            row = {"symbol": sym, "ratio": round(r, 3), "delta": delta, "stmt": sq, "sys": yq}
            if sq <= 0.001:
                missing_stmt.append(row)
            elif r < 0.95:
                partial.append(row)
            else:
                near_full.append(row)
        elif sq <= 0.001:
            missing_stmt.append(
                {"symbol": sym, "ratio": 0.0, "delta": delta, "stmt": sq, "sys": yq}
            )

        if story.get("hypothesis") == "POSSIBLE_MISSED_SELL":
            if sell_hints:
                # qty close?
                sell_q = sum(float(h["quantity"]) for h in sell_hints)
                if abs(sell_q - abs_d) <= max(0.51, abs_d * 0.05):
                    email_sell_explains.append(f"{sym} SELL {sell_q:g}≈|Δ|{abs_d:g}")
                else:
                    missed_sell_no_email.append(sym)  # sell email but wrong qty
            else:
                missed_sell_no_email.append(sym)

        trades = (story.get("recent_trades") or []) or []
        if not trades and story.get("hypothesis") in (
            "POSSIBLE_MISSED_SELL",
            "QUIET_GAP",
            None,
        ):
            # no recent trades in story window
            if not trades:
                quiet_no_trades.append(sym)

    contrib.sort(key=lambda x: -x[1])
    top5 = contrib[:5]
    top5_share = sum(c for _, c in top5) / total_abs
    med = median(ratios) if ratios else None

    return {
        "median_stmt_over_sys": round(med, 3) if med is not None else None,
        "missing_in_statement": missing_stmt,
        "partial_holdings": partial,
        "near_full_breaks": near_full,
        "reco_explained_symbols": reco_explained,
        "missed_sell_no_email": missed_sell_no_email,
        "email_sell_explains": email_sell_explains,
        "quiet_no_trades": quiet_no_trades,
        "top_gap_names": [{"symbol": s, "abs_delta": d} for s, d in top5],
        "top5_share_of_abs_delta": round(top5_share, 3),
        "partial_count": len(partial),
        "missing_count": len(missing_stmt),
    }


def _rank_explanations(
    *,
    primary_code: str,
    timing: Dict[str, Any],
    cluster: Dict[str, Any],
    if_exec: List[Dict[str, Any]],
    decomp: Dict[str, Any],
    break_count: int,
    system_higher: int,
    statement_higher: int,
    matched_count: int,
    total_abs: float,
    historical_sell_batch: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Score competing explanations 0..100. Higher = better fit to evidence.
    """
    ranked: List[Dict[str, Any]] = []
    ext = timing.get("extended_probe") or {}
    sell_batch = historical_sell_batch or {}

    def add(code, title, score, why, next_checks):
        ranked.append(
            {
                "code": code,
                "title": title,
                "score": int(max(0, min(100, score))),
                "why": why,
                "next_checks": next_checks,
            }
        )

    # Settlement
    if timing.get("applied"):
        add(
            "SETTLEMENT_LAG",
            "Settlement / as-of lag",
            95,
            timing.get("message") or "As-of shift clears most lines.",
            ["Re-run match at the shifted as-of and confirm with client."],
        )
    elif ext and not ext.get("would_clear"):
        add(
            "SETTLEMENT_LAG",
            "Settlement / as-of lag",
            5,
            f"Extended as-of probe best clear "
            f"{float(ext.get('cleared_ratio') or 0)*100:.0f}% — eliminated.",
            [],
        )

    # Historical SELL batch (full archive) — score from coverage only; no demotion of peers
    sell_cov = float(sell_batch.get("coverage_abs_delta") or 0)
    if sell_batch.get("found") and sell_cov > 0:
        exact = int(sell_batch.get("exact_count") or 0)
        n_hit = len(sell_batch.get("explained_symbols") or [])
        score = int(30 + 70 * min(1.0, sell_cov))
        emails = sell_batch.get("emails") or []
        when = ", ".join(str(e.get("sent_at") or "")[:10] for e in emails)
        subj = " | ".join((e.get("subject") or "")[:55] for e in emails)
        add(
            "HISTORICAL_SELL_BATCH_UNBOOKED",
            "Historical SELL reco email covers part of the system-long gap",
            score,
            sell_batch.get("message")
            or (
                f"Sell batch ({when}) covers {sell_cov*100:.0f}% of |Δ| "
                f"on {n_hit} names ({exact} exact)."
            ),
            [
                f"Review booking for: {subj}" if subj else "Review the matched SELL reco email(s).",
                "For covered names: book the sell, confirm partial fill, or note later buys.",
                "Names: "
                + ", ".join((sell_batch.get("explained_symbols") or [])[:15]),
            ],
        )

    # Reco cluster / if executed (near-as-of)
    if cluster.get("found"):
        add(
            "RECO_EMAIL_CLUSTER",
            "1–2 reco emails explain most gaps",
            90,
            cluster.get("message") or "Email cluster found.",
            ["Open those reco emails and book or reverse the lines."],
        )
    if if_exec:
        share = len(if_exec) / max(break_count, 1)
        add(
            "MISSED_RECO_EXECUTION",
            "Unbooked recommended trades",
            int(40 + 55 * share),
            f"{len(if_exec)}/{break_count} breaks close if recommended BUY/SELL booked.",
            ["Book or mark executed the open recommendations for: "
             + ", ".join(r.get("symbol") or "?" for r in if_exec[:8])],
        )

    # Partial demat / other account — scored from qty pattern only
    med = decomp.get("median_stmt_over_sys")
    partial_n = int(decomp.get("partial_count") or 0)
    missing_n = int(decomp.get("missing_count") or 0)
    if (
        system_higher >= max(5, break_count)
        and statement_higher == 0
        and med is not None
        and 0.35 <= med <= 0.92
        and partial_n >= max(3, break_count // 3)
    ):
        score = 70 + min(25, partial_n)
        note = (
            f"Median statement/system qty = {med:.0%} across breaks; "
            f"{partial_n} names only partly on this statement; "
            f"{missing_n} system-only (stmt 0). "
            f"{matched_count} names already match exactly. "
            f"Pattern can fit holdings split across DPs/accounts, "
            f"or unbooked sells (see ranked sell-batch probe if present)."
        )
        add(
            "PARTIAL_DEMAT_OR_OTHER_ACCOUNT",
            "Statement looks like a partial demat vs full books",
            score,
            note,
            [
                "Ask client which demat/broker this CDSL file is; compare to Inertia account scope.",
                "Pull the other DP/broker holding statement for the same as-of.",
                f"Largest gaps: "
                + ", ".join(
                    f"{x['symbol']} (|Δ| {x['abs_delta']:g})"
                    for x in (decomp.get("top_gap_names") or [])[:5]
                ),
            ],
        )

    # Unbooked sells (trade-story vs near-as-of window)
    miss_sell = decomp.get("missed_sell_no_email") or []
    if len(miss_sell) >= max(5, break_count // 3) and system_higher > statement_higher:
        why = (
            f"{len(miss_sell)} breaks look like system-long with no matching SELL "
            f"qty in the near-as-of window."
        )
        if sell_batch.get("message") and sell_cov > 0:
            why += f" Full-archive SELL scan: {sell_cov*100:.0f}% of |Δ| covered (see ranked list)."
        else:
            why += " Could be unbooked sells/transfers with no reco email on file."
        add(
            "UNBOOKED_SELLS_OR_TRANSFERS",
            "Many names look like sells/transfers not in books",
            55 + min(30, len(miss_sell)),
            why,
            [
                "Check contract notes / broker ledger for sells or off-market transfers.",
                "Sample names: " + ", ".join(miss_sell[:12]),
            ],
        )

    email_sell_ok = decomp.get("email_sell_explains") or []
    if email_sell_ok:
        add(
            "EMAIL_SELL_EXPLAINS_SOME",
            "Some gaps match emailed SELL qty",
            50 + min(40, 10 * len(email_sell_ok)),
            "Email SELL qty ≈ |Δ| for: " + "; ".join(email_sell_ok[:8]),
            ["Book those sells or confirm they settled after statement as-of."],
        )

    if missing_n and system_higher:
        add(
            "SYSTEM_ONLY_NAMES",
            "Names in books but absent from this statement",
            40 + min(30, missing_n * 10),
            (
                f"{missing_n} name(s) have statement qty 0 but system > 0: "
                + ", ".join(r["symbol"] for r in (decomp.get("missing_in_statement") or [])[:8])
            ),
            [
                "Confirm sold/transferred off this demat, or held elsewhere and still in Inertia books."
            ],
        )

    # Concentrated gaps
    top_share = float(decomp.get("top5_share_of_abs_delta") or 0)
    if top_share >= 0.45 and break_count >= 5:
        tops = decomp.get("top_gap_names") or []
        add(
            "CONCENTRATED_GAPS",
            "A few names dominate |Δ|",
            int(35 + 40 * top_share),
            (
                f"Top 5 names are {top_share*100:.0f}% of total |Δ qty| {total_abs:g}: "
                + ", ".join(f"{t['symbol']} ({t['abs_delta']:g})" for t in tops)
            ),
            [
                "Investigate those names first (trades, CA, other demat) before portfolio-wide fixes."
            ],
        )

    # Generic portfolio long fallback
    if primary_code == "PORTFOLIO_BOOKS_LONG" and not any(
        r["code"] == "PARTIAL_DEMAT_OR_OTHER_ACCOUNT" for r in ranked
    ):
        add(
            "PORTFOLIO_BOOKS_LONG",
            "Books systematically long vs this statement",
            50,
            f"Every break is system > statement ({system_higher}/{break_count}).",
            [
                "Compare full Inertia book to this demat file; check other accounts.",
                "Look for unbooked sells/transfers.",
            ],
        )

    ranked.sort(key=lambda r: (-r["score"], r["code"]))
    # Drop eliminated settlement (score < 15) from "best" display unless it's the only one
    return ranked


def run_investigation_passes(
    *,
    as_of: date,
    matched_count: int,
    breaks: List[Dict[str, Any]],
    timing: Optional[Dict[str, Any]] = None,
    email_cluster: Optional[Dict[str, Any]] = None,
    reco_if_executed_match: Optional[List[Dict[str, Any]]] = None,
    primary_cause: Optional[Dict[str, Any]] = None,
    historical_sell_batch: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run all investigation passes and rank explanations.

    Does **not** stop after Pass 1 portfolio pattern — continues until
    gap decomposition + ranked explanations are produced.
    """
    from services.reconciliation.diagnosis_service import infer_primary_cause

    timing = timing or {}
    cluster = email_cluster or {}
    if_exec = list(reco_if_executed_match or [])
    sell_batch = historical_sell_batch or {}
    break_count = len(breaks)
    system_higher = sum(1 for b in breaks if float(b.get("delta") or 0) < -0.001)
    statement_higher = sum(1 for b in breaks if float(b.get("delta") or 0) > 0.001)
    total_abs = sum(abs(float(b.get("delta") or 0)) for b in breaks)

    primary = primary_cause or infer_primary_cause(
        as_of=as_of,
        matched_count=matched_count,
        break_count=break_count,
        system_higher=system_higher,
        statement_higher=statement_higher,
        total_abs_delta=total_abs,
        timing=timing,
        email_cluster=cluster,
        reco_if_executed_match=if_exec,
    )

    passes: List[Dict[str, Any]] = []

    # Pass 0 — qty match census
    passes.append(
        _pass(
            0,
            "qty_match",
            findings=[
                f"As-of {as_of.isoformat()}: {matched_count} matched, "
                f"{break_count} breaks; |Δ qty| {total_abs:g}."
            ],
        )
    )

    # Pass 1 — portfolio direction (candidate only — keep iterating)
    direction_findings = [
        f"System higher on {system_higher}/{break_count} breaks; "
        f"statement higher on {statement_higher}."
    ]
    p1_cause = None
    if break_count >= 5 and system_higher >= break_count and statement_higher == 0:
        p1_cause = "PORTFOLIO_BOOKS_LONG"
        direction_findings.append(
            "Portfolio-wide system-long — continue probing for best explanation "
            "(do not stop here)."
        )
    elif break_count >= 5 and statement_higher >= break_count and system_higher == 0:
        p1_cause = "PORTFOLIO_STATEMENT_LONG"
        direction_findings.append(
            "Portfolio-wide statement-long — continue probing."
        )
    else:
        direction_findings.append("Mixed directions — continue to later passes.")
    passes.append(
        _pass(
            1,
            "portfolio_pattern",
            findings=direction_findings,
            candidate_cause=p1_cause,
            eliminated=["single_stock_email_buy"] if p1_cause else [],
            stop=False,
        )
    )

    # Pass 2 — as-of / settlement probe
    ext = timing.get("extended_probe") or {}
    lag_findings = [
        f"Short settle lag ({timing.get('lag_days', 0)}d): "
        f"applied={bool(timing.get('applied'))}, "
        f"cleared_ratio={timing.get('cleared_ratio') or 0}."
    ]
    if ext:
        shift = ext.get("shift_days")
        shift_s = f"{int(shift):+d}d" if shift is not None else "n/a"
        lag_findings.append(
            f"Extended as-of probe best shift {shift_s} "
            f"cleared {float(ext.get('cleared_ratio') or 0)*100:.0f}% "
            f"(threshold {float(ext.get('ratio_need') or 0)*100:.0f}%)."
        )
    p2_cause = None
    p2_stop = False
    if timing.get("applied"):
        p2_cause = "SETTLEMENT_LAG"
        lag_findings.append(timing.get("message") or "Settlement lag clears most lines.")
        p2_stop = True  # strong win — still keep later passes for residual detail
    elif ext and not ext.get("would_clear"):
        lag_findings.append(
            "Extended shifts do not clear — eliminate settlement lag as primary."
        )
    passes.append(
        _pass(
            2,
            "as_of_probe",
            findings=lag_findings,
            candidate_cause=p2_cause,
            eliminated=["SETTLEMENT_LAG"]
            if (not timing.get("applied") and ext and not ext.get("would_clear"))
            else [],
            stop=False,
        )
    )

    # Pass 3 — email / reco cluster
    p3_findings = []
    p3_cause = None
    if cluster.get("found"):
        p3_findings.append(cluster.get("message") or "Email cluster found.")
        p3_cause = "RECO_EMAIL_CLUSTER"
    else:
        p3_findings.append(
            cluster.get("message")
            or "No 1–2 reco emails explain most qty gaps."
        )
    if if_exec:
        p3_findings.append(
            f"{len(if_exec)} break(s) close if recommended trades were booked: "
            + ", ".join(r.get("symbol") or "?" for r in if_exec[:10])
            + ("…" if len(if_exec) > 10 else "")
        )
        if primary.get("code") == "MISSED_RECO_EXECUTION" or len(if_exec) >= max(
            3, break_count // 3
        ):
            p3_cause = p3_cause or "MISSED_RECO_EXECUTION"
    else:
        p3_findings.append(
            "Recommended BUY/SELL qty does not close most gaps if applied to books."
        )
    if p1_cause == "PORTFOLIO_BOOKS_LONG":
        p3_findings.append(
            "Post-as-of / BUY-only email tables do not explain system-long gaps."
        )
    passes.append(
        _pass(
            3,
            "email_reco_cluster",
            findings=p3_findings,
            candidate_cause=p3_cause,
            eliminated=["RECO_EMAIL_CLUSTER", "MISSED_RECO_EXECUTION"]
            if not p3_cause
            else [],
            stop=False,
        )
    )

    # Pass 4 — full-archive historical SELL batch (beyond ±45d near-as-of)
    sell_findings = []
    sell_cause = None
    if sell_batch.get("message"):
        sell_findings.append(sell_batch["message"])
        sell_findings.append(
            f"Meets ≥30% coverage bar: {bool(sell_batch.get('found'))}; "
            f"{len(sell_batch.get('explained_symbols') or [])} names, "
            f"{int(sell_batch.get('exact_count') or 0)} exact sell≈|Δ|."
        )
        if sell_batch.get("found"):
            sell_cause = "HISTORICAL_SELL_BATCH_UNBOOKED"
    else:
        sell_findings.append(
            "Full-archive scan: no SELL reco lines matched system-long breaks."
        )
    passes.append(
        _pass(
            4,
            "historical_sell_batch",
            findings=sell_findings,
            candidate_cause=sell_cause,
            stop=False,
        )
    )

    # Pass 4 — gap decomposition (the real iteration for portfolio-long)
    decomp = _gap_decomposition(breaks)
    p4_findings = [
        f"Median statement÷system qty among breaks: "
        f"{decomp['median_stmt_over_sys'] if decomp['median_stmt_over_sys'] is not None else 'n/a'}.",
        f"Partial on this statement (0 < stmt/sys < 0.95): {decomp['partial_count']}.",
        f"System-only (stmt 0): {decomp['missing_count']}"
        + (
            " — "
            + ", ".join(
                r["symbol"] for r in (decomp.get("missing_in_statement") or [])[:8]
            )
            if decomp["missing_count"]
            else ""
        )
        + ".",
        f"Top 5 names = {decomp['top5_share_of_abs_delta']*100:.0f}% of |Δ|: "
        + ", ".join(
            f"{t['symbol']} ({t['abs_delta']:g})"
            for t in (decomp.get("top_gap_names") or [])
        )
        + ".",
    ]
    if decomp.get("reco_explained_symbols"):
        p4_findings.append(
            "Reco-if-booked explains: "
            + ", ".join(decomp["reco_explained_symbols"][:12])
        )
    if decomp.get("email_sell_explains"):
        p4_findings.append(
            "Email SELL ≈ |Δ|: " + "; ".join(decomp["email_sell_explains"][:8])
        )
    if decomp.get("missed_sell_no_email"):
        p4_findings.append(
            f"System-long with no matching SELL in archive: "
            f"{len(decomp['missed_sell_no_email'])} names "
            f"({', '.join(decomp['missed_sell_no_email'][:10])}"
            f"{'…' if len(decomp['missed_sell_no_email']) > 10 else ''})."
        )
    passes.append(
        _pass(
            5,
            "gap_decomposition",
            findings=p4_findings,
            candidate_cause=None,
            stop=False,
        )
    )

    # Pass 6 — rank explanations
    explanations = _rank_explanations(
        primary_code=primary.get("code") or "MIXED_BREAKS",
        timing=timing,
        cluster=cluster,
        if_exec=if_exec,
        decomp=decomp,
        break_count=break_count,
        system_higher=system_higher,
        statement_higher=statement_higher,
        matched_count=matched_count,
        total_abs=total_abs,
        historical_sell_batch=sell_batch,
    )
    # Keep only meaningful scores for "best"
    best = [e for e in explanations if e["score"] >= 40][:5]
    if not best and explanations:
        best = explanations[:3]

    p5_findings = [
        f"#{i+1} [{e['score']}] {e['title']}: {e['why']}"
        for i, e in enumerate(best)
    ] or ["No strong explanation scored ≥40 — see mixed break rows."]
    passes.append(
        _pass(
            6,
            "rank_explanations",
            findings=p5_findings,
            candidate_cause=best[0]["code"] if best else None,
            stop=True,
        )
    )

    # Pass 7 — residual row notes pointer
    residual = [
        _sym(b)
        for b in breaks
        if not ((b.get("diagnosis") or {}).get("if_reco_applied") or {}).get(
            "explains_gap"
        )
    ]
    passes.append(
        _pass(
            7,
            "per_security_residual",
            findings=[
                f"{len(residual)} residual name(s) still have row-level 3-way notes "
                f"below — use after acting on top ranked explanation."
            ]
            + (
                ["Sample: " + ", ".join(residual[:12]) + ("…" if len(residual) > 12 else "")]
                if residual
                else []
            ),
            stop=False,
        )
    )

    # Promote best explanation into primary_cause for the banner
    if best:
        top = best[0]
        primary = {
            **primary,
            "code": top["code"],
            "title": top["title"],
            "summary": top["why"],
            "checks": list(top.get("next_checks") or [])[:5],
            "ranked_explanations": best,
        }
    else:
        primary = {**primary, "ranked_explanations": explanations[:5]}

    evidence_pack = {
        "as_of": as_of.isoformat(),
        "matched_count": matched_count,
        "break_count": break_count,
        "system_higher": system_higher,
        "statement_higher": statement_higher,
        "total_abs_delta_qty": total_abs,
        "primary_cause_code": primary.get("code"),
        "primary_cause_title": primary.get("title"),
        "primary_cause_summary": primary.get("summary"),
        "primary_cause_checks": list(primary.get("checks") or []),
        "ranked_explanations": best,
        "gap_decomposition": {
            "median_stmt_over_sys": decomp.get("median_stmt_over_sys"),
            "partial_count": decomp.get("partial_count"),
            "missing_count": decomp.get("missing_count"),
            "top_gap_names": decomp.get("top_gap_names"),
            "top5_share_of_abs_delta": decomp.get("top5_share_of_abs_delta"),
        },
        "investigation_passes": [
            {
                "pass": p["pass"],
                "name": p["name"],
                "findings": p["findings"],
                "candidate_cause": p["candidate_cause"],
                "eliminated": p["eliminated"],
                "stop": p["stop"],
            }
            for p in passes
        ],
        "stopped_at_pass": 6,
        "email_cluster_found": bool(cluster.get("found")),
        "reco_if_executed_count": len(if_exec),
        "timing_applied": bool(timing.get("applied")),
        "historical_sell_batch_found": bool(sell_batch.get("found")),
        "extended_probe": {
            "shift_days": ext.get("shift_days"),
            "cleared_ratio": ext.get("cleared_ratio"),
            "would_clear": ext.get("would_clear"),
        }
        if ext
        else None,
    }

    return {
        "investigation_passes": passes,
        "primary_cause": primary,
        "evidence_pack": evidence_pack,
        "stopped_at_pass": 6,
        "ranked_explanations": best,
        "gap_decomposition": decomp,
    }
