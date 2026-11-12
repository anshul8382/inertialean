"""Configurable analysis checklist — Investment-style categories on Capital IQ data.

Inspired by open checklists (telmus/crible categories) but:
- Data = uploaded Capital IQ / session only (no Yahoo/FMP deps)
- Default = verifiable flags, not composite scores
- Optional scores are opt-in via profile

Profile file: ~/.iis/checklist_profile.json (or IIS_CHECKLIST_PROFILE_PATH).
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from iis.config import settings

# Flag category → checklist bucket (telmus/crible-inspired naming)
FLAG_CATEGORY_TO_BUCKET: dict[str, str] = {
    "cash_flow": "earnings_quality",
    "working_capital": "working_capital",
    "margins": "profitability",
    "leverage": "balance_sheet_health",
    "guidance": "guidance_track",
    "reconciliation": "data_integrity",
    "industry": "industry_context",
    "segment": "industry_context",
}

DEFAULT_BUCKETS: dict[str, bool] = {
    "earnings_quality": True,
    "working_capital": True,
    "profitability": True,
    "balance_sheet_health": True,
    "guidance_track": True,
    "data_integrity": True,
    "industry_context": True,
}

BUCKET_LABELS: dict[str, str] = {
    "earnings_quality": "Earnings quality / cash",
    "working_capital": "Working capital",
    "profitability": "Profitability / margins",
    "balance_sheet_health": "Balance sheet health",
    "guidance_track": "Guidance track record",
    "data_integrity": "Data integrity",
    "industry_context": "Industry context",
}

PACK_SECTION_LABELS: dict[str, str] = {
    "sources": "Sources on file",
    "checklist": "Checklist summary",
    "headline": "Headline metrics",
    "pnl": "P&L / cash",
    "ratios": "Key ratios",
    "capex": "Capex analysis",
    "growth": "Revenue growth",
    "estimates": "Estimates / multiples",
    "flags": "Data flags",
    "quality_scores": "Quality scores (optional)",
    "valuation": "Valuation",
    "capacity": "Capacity excerpts",
    "formulas": "Formula definitions",
}

THRESHOLD_LABELS: dict[str, str] = {
    "rev_growth_min_for_ocf_check": "Min revenue YoY % to check OCF lag",
    "ocf_lag_vs_rev_pp": "OCF lag vs revenue (pp)",
    "ocf_to_ni_min": "Min CFO / Net income",
    "ar_vs_rev_extra_pp": "Receivables YoY vs revenue (extra pp)",
    "inventory_vs_rev_extra_pp": "Inventory YoY vs revenue (extra pp)",
    "receivable_days_increase": "Receivable days increase trigger",
    "rev_growth_min_for_margin_check": "Min revenue YoY % for margin checks",
    "gross_margin_drop": "Gross margin drop (decimal, 0.01 = 1pp)",
    "ebitda_margin_drop": "EBITDA margin drop (decimal)",
    "debt_vs_rev_extra_pp": "Debt YoY vs revenue (extra pp)",
    "interest_coverage_drop_ratio": "Interest coverage vs prior (ratio)",
    "reconciliation_gap_pct": "CIQ vs AR gap %",
}

DEFAULT_PACK_SECTIONS: dict[str, bool] = {
    "sources": True,
    "checklist": True,
    "headline": True,
    "pnl": True,
    "ratios": True,
    "capex": True,
    "growth": True,
    "estimates": True,
    "flags": True,
    "quality_scores": False,  # opt-in composite / F-score style
    "valuation": True,
    "capacity": True,
    "formulas": True,
}


@dataclass
class ChecklistThresholds:
    """Tunable triggers for verifiable flags (percent points unless noted)."""

    rev_growth_min_for_ocf_check: float = 5.0  # % YoY
    ocf_lag_vs_rev_pp: float = 5.0
    ocf_to_ni_min: float = 0.70  # CFO >= 70% of NI
    ar_vs_rev_extra_pp: float = 5.0
    inventory_vs_rev_extra_pp: float = 5.0
    receivable_days_increase: float = 10.0  # days
    rev_growth_min_for_margin_check: float = 3.0
    gross_margin_drop: float = 0.01  # absolute (1 pp if margin is decimal)
    ebitda_margin_drop: float = 0.005
    debt_vs_rev_extra_pp: float = 10.0
    interest_coverage_drop_ratio: float = 0.85  # flag if IC < 85% of prior
    reconciliation_gap_pct: float = 5.0


@dataclass
class ChecklistProfile:
    name: str = "default"
    description: str = "CIQ-first research checklist (verifiable facts; scores off)"
    buckets: dict[str, bool] = field(default_factory=lambda: deepcopy(DEFAULT_BUCKETS))
    pack_sections: dict[str, bool] = field(default_factory=lambda: deepcopy(DEFAULT_PACK_SECTIONS))
    thresholds: ChecklistThresholds = field(default_factory=ChecklistThresholds)
    # Optional composite scores (Piotroski-style) — off by default
    enable_quality_scores: bool = False
    # Ratio keys from RATIO_CATALOG / formulas to emphasize in pack
    highlight_ratios: list[str] = field(
        default_factory=lambda: [
            "gross_margin",
            "ebitda_margin",
            "roe",
            "roa",
            "debt_to_equity",
            "net_debt_to_ebitda",
            "interest_coverage",
            "fcf_margin",
            "capex_to_sales",
            "cfo_to_ebitda",
            "receivable_days",
            "cfo_to_ni",
        ]
    )

    def bucket_enabled(self, bucket: str) -> bool:
        return bool(self.buckets.get(bucket, True))

    def flag_category_enabled(self, category: str) -> bool:
        bucket = FLAG_CATEGORY_TO_BUCKET.get(category, category)
        return self.bucket_enabled(bucket)

    def section_enabled(self, section: str) -> bool:
        if section == "quality_scores":
            return bool(self.enable_quality_scores and self.pack_sections.get("quality_scores", False))
        return bool(self.pack_sections.get(section, True))


def default_profile() -> ChecklistProfile:
    return ChecklistProfile()


def profile_path() -> Path:
    custom = getattr(settings, "checklist_profile_path", None)
    if custom:
        return Path(custom)
    # Prefer JSON (no PyYAML dependency); accept legacy .yaml path if user set it
    return settings.data_dir / "checklist_profile.json"


def _thresholds_from_dict(raw: dict[str, Any] | None) -> ChecklistThresholds:
    base = ChecklistThresholds()
    if not raw:
        return base
    allowed = {f.name for f in base.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kwargs = {k: v for k, v in raw.items() if k in allowed}
    return ChecklistThresholds(**{**asdict(base), **kwargs})


def profile_from_dict(data: dict[str, Any]) -> ChecklistProfile:
    buckets = deepcopy(DEFAULT_BUCKETS)
    buckets.update(data.get("buckets") or {})
    sections = deepcopy(DEFAULT_PACK_SECTIONS)
    sections.update(data.get("pack_sections") or {})
    return ChecklistProfile(
        name=str(data.get("name") or "default"),
        description=str(data.get("description") or ""),
        buckets=buckets,
        pack_sections=sections,
        thresholds=_thresholds_from_dict(data.get("thresholds")),
        enable_quality_scores=bool(data.get("enable_quality_scores", False)),
        highlight_ratios=list(data.get("highlight_ratios") or ChecklistProfile().highlight_ratios),
    )


def profile_to_dict(profile: ChecklistProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "description": profile.description,
        "buckets": dict(profile.buckets),
        "pack_sections": dict(profile.pack_sections),
        "thresholds": asdict(profile.thresholds),
        "enable_quality_scores": profile.enable_quality_scores,
        "highlight_ratios": list(profile.highlight_ratios),
    }


def checklist_ui_payload(profile: ChecklistProfile | None = None) -> dict[str, Any]:
    """Profile + display labels for the web UI."""
    p = profile or load_checklist_profile()
    return {
        "path": str(profile_path()),
        "profile": profile_to_dict(p),
        "labels": {
            "buckets": BUCKET_LABELS,
            "pack_sections": PACK_SECTION_LABELS,
            "thresholds": THRESHOLD_LABELS,
        },
    }


def apply_checklist_update(data: dict[str, Any]) -> ChecklistProfile:
    """Merge a partial UI/API update onto the saved profile and persist."""
    current = profile_to_dict(load_checklist_profile())
    if "name" in data and data["name"] is not None:
        current["name"] = str(data["name"])
    if "description" in data and data["description"] is not None:
        current["description"] = str(data["description"])
    if isinstance(data.get("buckets"), dict):
        current["buckets"].update({k: bool(v) for k, v in data["buckets"].items() if k in DEFAULT_BUCKETS})
    if isinstance(data.get("pack_sections"), dict):
        current["pack_sections"].update(
            {k: bool(v) for k, v in data["pack_sections"].items() if k in DEFAULT_PACK_SECTIONS}
        )
    if isinstance(data.get("thresholds"), dict):
        merged = dict(current.get("thresholds") or {})
        allowed = set(asdict(ChecklistThresholds()).keys())
        for k, v in data["thresholds"].items():
            if k not in allowed:
                continue
            try:
                merged[k] = float(v)
            except (TypeError, ValueError):
                continue
        current["thresholds"] = merged
    if "enable_quality_scores" in data and data["enable_quality_scores"] is not None:
        current["enable_quality_scores"] = bool(data["enable_quality_scores"])
        current["pack_sections"]["quality_scores"] = bool(data["enable_quality_scores"])
    if isinstance(data.get("highlight_ratios"), list):
        current["highlight_ratios"] = [str(x) for x in data["highlight_ratios"] if x]
    profile = profile_from_dict(current)
    save_checklist_profile(profile)
    return profile


def load_checklist_profile(path: Path | None = None) -> ChecklistProfile:
    """Load JSON profile if present; otherwise default (and write default once)."""
    target = path or profile_path()
    if not target.exists():
        p = default_profile()
        try:
            save_checklist_profile(p, target)
        except OSError:
            pass
        return p
    try:
        data = json.loads(target.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return default_profile()
        return profile_from_dict(data)
    except Exception:
        return default_profile()


def save_checklist_profile(profile: ChecklistProfile, path: Path | None = None) -> Path:
    target = path or profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(profile_to_dict(profile), indent=2)
    target.write_text(text, encoding="utf-8")
    return target


def filter_flags_by_profile(flags: list[Any], profile: ChecklistProfile | None = None) -> list[Any]:
    profile = profile or load_checklist_profile()
    return [f for f in flags if profile.flag_category_enabled(getattr(f, "category", "") or "")]


def checklist_summary_markdown(profile: ChecklistProfile, *, flag_counts: dict[str, int] | None = None) -> list[str]:
    """Pack section: which buckets are on and how many flags fired."""
    lines = [
        "## Analysis checklist",
        "",
        f"_Profile: **{profile.name}** — {profile.description or 'CIQ-first checklist'}_",
        "",
        "| Bucket | Enabled | Flags (review) |",
        "|---|:---:|---:|",
    ]
    flag_counts = flag_counts or {}
    # Display order
    order = [
        ("earnings_quality", "Earnings quality / cash"),
        ("working_capital", "Working capital"),
        ("profitability", "Profitability / margins"),
        ("balance_sheet_health", "Balance sheet health"),
        ("guidance_track", "Guidance track record"),
        ("data_integrity", "Data integrity"),
        ("industry_context", "Industry context"),
    ]
    for key, label in order:
        on = "yes" if profile.bucket_enabled(key) else "no"
        n = flag_counts.get(key, 0)
        lines.append(f"| {label} | {on} | {n if profile.bucket_enabled(key) else '—'} |")
    lines.append("")
    lines.append(
        f"- Optional quality scores (Piotroski-style): "
        f"**{'on' if profile.enable_quality_scores else 'off'}** "
        f"(edit `~/.iis/checklist_profile.json` → `enable_quality_scores`)"
    )
    lines.append("")
    return lines
