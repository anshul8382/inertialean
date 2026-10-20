"""Broker trade / contract-note parsers (Drive Trade details folder)."""

from services.reconciliation.trade_parsers.detect import detect_and_parse_trade

__all__ = ["detect_and_parse_trade"]
