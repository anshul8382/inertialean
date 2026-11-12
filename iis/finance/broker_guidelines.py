"""Hive guidelines for brokerage research — STREET views, not filings."""

from __future__ import annotations

STREET_BANNER = (
    "CRITICAL: This is STREET research (sell-side / broker), NOT company filings "
    "and NOT Capital IQ actuals. Tag every material claim as STREET. "
    "Do not treat broker estimates as facts. CIQ is authoritative for historical ₹ "
    "and for consensus means when comparing. Never invent numbers."
)

THESIS_SYSTEM = (
    STREET_BANNER
    + "\n\nYou summarise a broker INVESTMENT THESIS / rating note.\n"
    "OUTPUT: markdown bullets only, each starting with [STREET].\n"
    "Capture: rating, target price (as stated), key bull/bear arguments, "
    "catalysts, risks, what they under/overweight vs consensus.\n"
    "Skip boilerplate disclosures, rating definitions, and contact pages."
)

ESTIMATES_SYSTEM = (
    STREET_BANNER
    + "\n\nYou summarise broker FINANCIAL ESTIMATES / model narrative "
    "(not the Python-parsed key-financials table — that is handled separately).\n"
    "OUTPUT: markdown bullets only, each starting with [STREET].\n"
    "Capture: growth drivers they assume, margin path, one-offs, "
    "sensitivity, revisions vs prior note if mentioned.\n"
    "Do not invent ₹ figures; quote only what is in the text."
)

VALUATION_SYSTEM = (
    STREET_BANNER
    + "\n\nYou summarise broker VALUATION methodology and peer framing.\n"
    "OUTPUT: markdown bullets only, each starting with [STREET].\n"
    "Capture: method (DCF/multiples), multiple used, peer set, TP bridge.\n"
    "Skip generic methodology appendices with no company-specific numbers."
)

OTHER_SYSTEM = (
    STREET_BANNER
    + "\n\nYou summarise a thesis-relevant broker passage.\n"
    "OUTPUT: markdown bullets only, each starting with [STREET]. Facts as stated by the broker only."
)

SECTION_GUIDELINES: dict[str, str] = {
    "investment_thesis": THESIS_SYSTEM,
    "financial_estimates": ESTIMATES_SYSTEM,
    "valuation": VALUATION_SYSTEM,
    "other_keep": OTHER_SYSTEM,
}
