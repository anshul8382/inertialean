"""
Risk assessment scoring: question points → time horizon + risk tolerance → profile matrix.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --- Question point maps (locked) ---

WITHDRAW_BEGIN_POINTS = {
    "Less than 3 years": 1,
    "3–5 years": 3,
    "3-5 years": 3,
    "6–10 years": 7,
    "6-10 years": 7,
    "11 years or more": 10,
}

SPEND_DOWN_POINTS = {
    "Less than 2 years": 0,
    "2–5 years": 1,
    "2-5 years": 1,
    "6–10 years": 4,
    "6-10 years": 4,
    "11 years or more": 8,
}

KNOWLEDGE_POINTS = {
    "None": 1,
    "Limited": 3,
    "Good": 7,
    "Extensive": 10,
}

ATTITUDE_POINTS = {
    "Most concerned about my investment losing value": 1,
    "Equally concerned about my investment losing or gaining value": 4,
    "Most concerned about my investment gaining value": 8,
}

HOLDINGS_POINTS = {
    "Fixed Deposits": 3,
    "Bonds/Debt Mutual Funds": 6,
    "Direct Stocks/Equity Mutual Funds": 8,
}

CRASH_POINTS = {
    "Sell all of my shares": 0,
    "Sell some of my shares": 2,
    "Do nothing": 5,
    "Buy more shares": 8,
}

CHART_POINTS = {
    "A": 0,
    "B": 3,
    "C": 6,
    "D": 8,
    "E": 10,
}

# Matrix bands: (min_h, max_h) -> list of (max_risk_inclusive, profile) in order
_MATRIX: List[Tuple[int, int, List[Tuple[int, str]]]] = [
    (3, 4, [(18, "Conservative"), (31, "Moderately Conservative"), (40, "Moderate")]),
    (5, 5, [
        (15, "Conservative"),
        (24, "Moderately Conservative"),
        (34, "Moderate"),
        (40, "Moderately Aggressive"),
    ]),
    (7, 9, [
        (12, "Conservative"),
        (20, "Moderately Conservative"),
        (28, "Moderate"),
        (36, "Moderately Aggressive"),
        (40, "Aggressive"),
    ]),
    (10, 12, [
        (10, "Conservative"),
        (18, "Moderately Conservative"),
        (26, "Moderate"),
        (34, "Moderately Aggressive"),
        (40, "Aggressive"),
    ]),
    (14, 18, [
        (10, "Conservative"),
        (15, "Moderately Conservative"),
        (23, "Moderate"),
        (31, "Moderately Aggressive"),
        (40, "Aggressive"),
    ]),
]

MAX_EQUITY_PCT = {
    "Conservative": 20,
    "Moderately Conservative": 30,
    "Moderate": 50,
    "Moderately Aggressive": 70,
    "Aggressive": 100,
}

# Client model uses lowercase short names
PROFILE_TO_CLIENT = {
    "Conservative": "conservative",
    "Moderately Conservative": "moderately_conservative",
    "Moderate": "moderate",
    "Moderately Aggressive": "moderately_aggressive",
    "Aggressive": "aggressive",
}

_BAND_CENTERS = [(3, 4), (5, 5), (7, 9), (10, 12), (14, 18)]


def _norm(s: Any) -> str:
    return (str(s) if s is not None else "").strip()


def nearest_horizon_band(score: int) -> Tuple[int, int]:
    """Map any horizon total to nearest matrix band (handles gaps 1–2, 6, 13)."""
    score = max(0, int(score))
    best = _BAND_CENTERS[0]
    best_dist = abs(score - (best[0] + best[1]) / 2)
    for lo, hi in _BAND_CENTERS:
        mid = (lo + hi) / 2
        dist = abs(score - mid)
        if dist < best_dist or (dist == best_dist and lo <= score <= hi):
            best = (lo, hi)
            best_dist = dist
    # Prefer containing band when score is inside one
    for lo, hi in _BAND_CENTERS:
        if lo <= score <= hi:
            return lo, hi
    return best


def profile_from_scores(time_horizon: int, risk_tolerance: int) -> str:
    """Apply locked matrix; risk tolerance capped at 40."""
    rt = max(0, min(40, int(risk_tolerance)))
    lo, hi = nearest_horizon_band(time_horizon)
    for band_lo, band_hi, cuts in _MATRIX:
        if band_lo == lo and band_hi == hi:
            for max_rt, profile in cuts:
                if rt <= max_rt:
                    return profile
            return cuts[-1][1]
    return "Moderate"


def holdings_points(selected: Optional[Iterable[str]]) -> int:
    """Multi-select: use highest selected points (not sum)."""
    if not selected:
        return 0
    best = 0
    for item in selected:
        pts = HOLDINGS_POINTS.get(_norm(item), 0)
        if pts > best:
            best = pts
    return best


def compute_time_horizon(withdraw_begin: str, spend_down: str) -> int:
    return WITHDRAW_BEGIN_POINTS.get(_norm(withdraw_begin), 0) + SPEND_DOWN_POINTS.get(
        _norm(spend_down), 0
    )


def _chart_points(chart: str) -> int:
    raw = _norm(chart)
    if raw in CHART_POINTS:
        return CHART_POINTS[raw]
    if raw and raw[0].upper() in CHART_POINTS:
        return CHART_POINTS[raw[0].upper()]
    return 0


def compute_risk_tolerance(
    knowledge: str,
    attitude: str,
    holdings: Optional[Sequence[str]],
    crash: str,
    chart: str,
) -> int:
    total = (
        KNOWLEDGE_POINTS.get(_norm(knowledge), 0)
        + ATTITUDE_POINTS.get(_norm(attitude), 0)
        + holdings_points(holdings)
        + CRASH_POINTS.get(_norm(crash), 0)
        + _chart_points(chart)
    )
    return min(40, total)


def validate_assessment_answers(answers: Dict[str, Any]) -> List[str]:
    invalid: List[str] = []
    choice_maps = {
        "withdraw_begin": WITHDRAW_BEGIN_POINTS,
        "spend_down": SPEND_DOWN_POINTS,
        "knowledge": KNOWLEDGE_POINTS,
        "attitude": ATTITUDE_POINTS,
        "crash": CRASH_POINTS,
    }
    for field, choices in choice_maps.items():
        if _norm(answers.get(field)) not in choices:
            invalid.append(field)

    holdings = answers.get("holdings") or []
    if isinstance(holdings, str):
        holdings = [h.strip() for h in holdings.split(",") if h.strip()]
    if not holdings or any(_norm(item) not in HOLDINGS_POINTS for item in holdings):
        invalid.append("holdings")

    chart = _norm(answers.get("chart"))
    if chart not in CHART_POINTS:
        invalid.append("chart")
    return invalid


def score_assessment(answers: Dict[str, Any]) -> Dict[str, Any]:
    """
    Score a submitted answers dict.

    Expected keys: withdraw_begin, spend_down, knowledge, attitude,
    holdings (list), crash, chart, name, email, investments_narrative (optional).
    """
    holdings = answers.get("holdings") or []
    if isinstance(holdings, str):
        holdings = [h.strip() for h in holdings.split(",") if h.strip()]

    invalid = validate_assessment_answers({**answers, "holdings": holdings})
    if invalid:
        raise ValueError("Invalid risk assessment answer(s): " + ", ".join(sorted(set(invalid))))

    th = compute_time_horizon(
        answers.get("withdraw_begin") or "",
        answers.get("spend_down") or "",
    )
    rt = compute_risk_tolerance(
        answers.get("knowledge") or "",
        answers.get("attitude") or "",
        holdings,
        answers.get("crash") or "",
        answers.get("chart") or "",
    )
    profile = profile_from_scores(th, rt)
    band = nearest_horizon_band(th)
    return {
        "time_horizon_score": th,
        "risk_tolerance_score": rt,
        "horizon_band": f"{band[0]}-{band[1]}",
        "risk_profile": profile,
        "max_equity_pct": MAX_EQUITY_PCT.get(profile, 50),
        "client_risk_profile": PROFILE_TO_CLIENT.get(profile, "moderate"),
    }


QUESTION_OPTIONS = {
    "withdraw_begin": list(dict.fromkeys([
        "Less than 3 years",
        "3–5 years",
        "6–10 years",
        "11 years or more",
    ])),
    "spend_down": list(dict.fromkeys([
        "Less than 2 years",
        "2–5 years",
        "6–10 years",
        "11 years or more",
    ])),
    "knowledge": list(KNOWLEDGE_POINTS.keys()),
    "attitude": list(ATTITUDE_POINTS.keys()),
    "holdings": list(HOLDINGS_POINTS.keys()),
    "crash": list(CRASH_POINTS.keys()),
    "chart": [
        ("A", "Plan A — most conservative range"),
        ("B", "Plan B — moderately conservative"),
        ("C", "Plan C — moderate"),
        ("D", "Plan D — moderately aggressive"),
        ("E", "Plan E — most aggressive range"),
    ],
}

ANSWER_LABELS = {
    "withdraw_begin": "When will you begin withdrawing?",
    "spend_down": "How long will you spend down investments?",
    "knowledge": "Investment knowledge",
    "attitude": "Attitude toward risk",
    "holdings": "Current holdings",
    "crash": "If markets fell sharply…",
    "chart": "Preferred outcome range (chart)",
    "investments_narrative": "Current investments (notes)",
}


def format_risk_submission_for_display(submission) -> Dict[str, Any]:
    """Human-readable answers + evaluation for CRM / proposal surfaces."""
    answers = getattr(submission, "answers_json", None) or {}
    if not isinstance(answers, dict):
        answers = {}

    rows: List[Dict[str, str]] = []
    for key, label in ANSWER_LABELS.items():
        raw = answers.get(key)
        if raw is None or raw == "":
            continue
        if key == "holdings" and isinstance(raw, (list, tuple)):
            value = ", ".join(str(x) for x in raw)
        elif key == "chart":
            chart_map = {c[0]: c[1] for c in QUESTION_OPTIONS["chart"]}
            code = _norm(raw)
            value = chart_map.get(code[:1].upper() if code else "", str(raw))
        else:
            value = str(raw)
        rows.append({"key": key, "label": label, "value": value})

    profile = getattr(submission, "risk_profile", None) or ""
    return {
        "name": getattr(submission, "name", None) or answers.get("name") or "",
        "email": getattr(submission, "email", None) or answers.get("email") or "",
        "answer_rows": rows,
        "time_horizon_score": getattr(submission, "time_horizon_score", None),
        "risk_tolerance_score": getattr(submission, "risk_tolerance_score", None),
        "horizon_band": getattr(submission, "horizon_band", None),
        "risk_profile": profile,
        "max_equity_pct": getattr(submission, "max_equity_pct", None),
        "client_risk_profile": getattr(submission, "client_risk_profile", None),
        "created_at": getattr(submission, "created_at", None),
        "summary_line": (
            f"{profile} — time horizon {getattr(submission, 'time_horizon_score', '?')}, "
            f"risk tolerance {getattr(submission, 'risk_tolerance_score', '?')}, "
            f"max equity {getattr(submission, 'max_equity_pct', '?')}%"
        ),
    }


def risk_details_for_proposal(submission) -> Dict[str, str]:
    """Compact fields merged into proposal details / DOCX."""
    if not submission:
        return {}
    view = format_risk_submission_for_display(submission)
    return {
        "risk_profile": view.get("risk_profile") or "",
        "risk_evaluation": view.get("summary_line") or "",
        "max_equity_pct": (
            f"{view['max_equity_pct']}%" if view.get("max_equity_pct") is not None else ""
        ),
        "time_horizon_score": str(view.get("time_horizon_score") or ""),
        "risk_tolerance_score": str(view.get("risk_tolerance_score") or ""),
    }
