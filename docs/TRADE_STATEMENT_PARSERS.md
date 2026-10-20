# Trade statement parsers (Drive)

## Samples collected (client 99 Trade details)

20 files from Drive folder `16n8i0xNG72trLApjrtObXZ9o7J1ZUtfM` → `tests/fixtures/trade_statements/_raw/`.

| Format | Files | Status |
|--------|------:|--------|
| Zerodha contract note PDF | 15 | **Parsed** — order lines → symbol/qty/side/ISIN |
| Zerodha Console tradebook XLSX | 1 | **Parsed** — Symbol/ISIN/Trade Date/Type/Qty |
| Zerodha holdings SOA PDF | 1 | Detected & **skipped** (positions, not trades) |
| Angel One order-book JPEG | 2 | Classified; fills need **OCR** (tesseract) |
| ICICI Direct order-book JPEG | 1 | Classified; fills need **OCR** |

## Code

- `services/reconciliation/trade_parsers/` — detect + format parsers
- `collect_broker_fills_from_drive()` uses `detect_and_parse_trade()`
- **Symbol mapping:** `map_trade_fills()` in `security_resolve.py` — same
  `var/holdings_reconciliation/security_maps.json` as holdings (ISIN / NSE /
  broker_code / name + Remember). Trade fills keep `broker_symbol` and set
  `symbol` to the canonical `Security.symbol` when mapped.
- Audit: `python3 scripts/audit_trade_drive_formats.py --client-id 99`

## Screenshots (OCR)

Needs `tesseract` binary + `pytesseract` (in `requirements.txt`).

**Local (this Mac):** micromamba install under `.tools/tesseract/` — auto-discovered by
`services/reconciliation/trade_parsers/ocr_env.py`. Or set `TESSERACT_CMD` /
`TESSDATA_PREFIX`.

**KVM (AlmaLinux):**
```bash
sudo dnf install -y tesseract tesseract-langpack-eng
# venv
cd /opt/Inertia2026v1 && ./venv/bin/pip install pytesseract
```

OCR text fixtures under `text_snippets/` also cover Angel/ICICI row parsing without the binary.

## Temp lab UI (local)

Hub → **Trade parser lab** (`/hub/trade-parser-lab/`) — upload or pick a preloaded
fixture; image/PDF on the left, fills + raw OCR on the right.

Enable with Flask debug **or** `TRADE_PARSER_LAB=1` in `.env`.

## Extra Drive folders

Samples from user-shared Trade folders live under
`tests/fixtures/trade_statements/_raw_extra/` (Groww-style contract PDFs, Abhinav
JPEGs, Trades_History XLSX, share `.xls`).

## Not yet covered

Groww encrypted contract PDFs (need password / OCR of scanned pages), legacy `.xls`
tab-text exports, Groww Trades_History column map — extend parsers as lab catches them.
