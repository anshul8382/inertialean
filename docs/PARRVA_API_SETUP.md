# PaRRVA API Setup

This document describes how to configure and use the PaRRVA portfolio submission feature in Inertia.

## Overview

The PaRRVA integration allows advisors to:
1. **Select equity portfolio** – Choose which equity holdings from a client's portfolio to submit
2. **Add MF holdings manually** – Enter mutual fund holdings (fund name, ISIN, amount/units) since MF portfolio is not stored in the system
3. **Submit to PaRRVA API** – Send the combined data to PaRRVA

## Configuration

Set these environment variables (or add to `.env`):

```bash
PARRVA_API_URL=https://api.parrva.example.com   # Base URL (no trailing slash)
PARRVA_API_KEY=your_api_key_here
```

## UI Access

- **Navigation**: Portfolio Management → Submit to PaRRVA
- **Direct URL**: `/parrva/submit`
- **From client**: Select client from list, or add a "Submit to PaRRVA" link on client details page

## Payload Structure (Current Implementation)

The service builds a payload in this format. **Adjust `services/parrva_service.py`** to match your actual PaRRVA API documentation.

### Request

- **Method**: POST
- **URL**: `{PARRVA_API_URL}/portfolio/submit`
- **Headers**: `Authorization: Bearer {API_KEY}`, `Content-Type: application/json`

### Body (example)

```json
{
  "client_reference": "123",
  "client_name": "John Doe",
  "equity_holdings": [
    {
      "symbol": "RELIANCE",
      "name": "Reliance Industries Ltd",
      "quantity": 100,
      "current_price": 2500.50,
      "current_value": 250050
    }
  ],
  "mutual_fund_holdings": [
    {
      "fund_name": "HDFC Top 100",
      "isin": "INE123456789",
      "amount": 50000,
      "units": 100.5
    }
  ]
}
```

### Adjusting for Your API

If your PaRRVA API documentation specifies different:
- **Endpoint path** – Change `url` in `ParrvaService.submit_portfolio()` (e.g. `/v1/portfolio`, `/api/submit`)
- **Field names** – Update `build_equity_payload()` and `build_mf_payload()` in `services/parrva_service.py`
- **Auth** – Update headers (e.g. `X-API-Key` only, or different header name)

## Files

| File | Purpose |
|------|---------|
| `services/parrva_service.py` | API client, payload building |
| `routes/parrva.py` | Routes, holdings fetch |
| `templates/parrva/submit.html` | UI for client selection, equity checkboxes, MF form |

## Adding a Client Details Link

To add "Submit to PaRRVA" on the client details page, add a button in `templates/clients/client_details.html`:

```html
<a href="{{ url_for('parrva.submit_page', client_id=client.id) }}" class="btn btn-sm btn-outline-primary">
    <i class="fas fa-paper-plane"></i> Submit to PaRRVA
</a>
```
