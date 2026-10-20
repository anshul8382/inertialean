# Trade statement fixtures (Drive samples)

Pulled from client **99** Trade details Drive folder (`16n8i0xNG72trLApjrtObXZ9o7J1ZUtfM`).

| Kind | Count | Parser |
|------|------:|--------|
| Zerodha contract note PDF | ~15 | `zerodha_contract_note` |
| Zerodha tradebook xlsx | 1 | `zerodha_tradebook_xlsx` |
| Zerodha holdings SOA PDF | 1 | detected, skipped (not trades) |
| Order-book screenshots (Angel / ICICI) | 3 | `order_book_screenshot` (needs OCR) |

Binaries live under `_raw/`, `zerodha_pdf/`, `zerodha_xlsx/`, `screenshots/`.
OCR text stand-ins for screenshots: `text_snippets/`.

Re-collect:
```bash
# uses trade_drive_links.json client 99
python3 scripts/audit_trade_drive_formats.py --download 25
```
