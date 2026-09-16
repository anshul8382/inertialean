# Trade edit — cashflow review (ops guide)

**Audience:** Advisors and ops editing BUY/SELL trades in Inertia.

**Why this exists:** Trades and cashflows are stored separately. There is **no link** from a trade row to its cashflow row. When you change quantity, price, date, or type on a trade, **cashflow is not updated automatically** in a reliable way.

---

## When you must read this

- After **editing** a transaction (`/transaction/<id>/edit`)
- After changing **price**, **quantity**, **date**, or **BUY/SELL** on an existing trade
- When cashflow↔trade integrity flags a mismatch for that client/date

---

## What the system does on trade save

1. **Trade row** — updated (holdings adjusted best-effort).
2. **Cashflow** — **new trades** still create a matching cashflow line. **Edits and deletes** of a single trade do **not** change cashflow. Use **Edit** on the cashflows list (detail view) to adjust amount/date, or add a new row.
3. **You** — after a trade **edit**, confirm in the UI that you will review cashflows when amounts or dates change.

**Bulk trade upload** may still write aggregated **net cashflow per day** rows. Single-trade **create** writes one cashflow line; single-trade **edit/delete** does not.

---

## What you should do (checklist)

1. **Note the client and trade date** before or after saving.
2. Open **Cashflows** for that client (filter by date if needed).
3. Find cashflow row(s) for that date:
   - Advisor **manual notes** (month-wise or split entries)
   - Auto lines like `BUY … shares of …` or `Net cashflow for YYYY-MM-DD`
4. **Update amounts** so they match the corrected trade economics:
   - **BUY** → cashflow should reflect money **into** the portfolio (typically negative amount / INFLOW in the app).
   - **SELL** → cashflow should reflect proceeds **out** (typically positive / OUTFLOW).
5. If the trade **date changed**, check **both** the old date and the new date — cashflow on the old date may need removal or adjustment.
6. If unsure, run **Cashflow ↔ trades** on the client details integrity page and re-check.

---

## Same-day multiple cashflows

You **can** add more than one cashflow on the same calendar day (e.g. split advisor notes). The cashflows list can show **detail** or **aggregated by date** — use detail view when fixing a specific line.

---

## Do not assume

- Saving the trade **does not guarantee** cashflow is correct.
- XIRR and period analysis use **advisor cashflows** — wrong cashflow breaks performance numbers even if holdings look fine.

---

## Related screens

| Screen | Path |
|--------|------|
| Edit trade | `/transaction/<id>/edit` |
| Client cashflows | `/cashflows?client_id=<id>` |
| Add cashflow | `/cashflows/add` |
| Edit cashflow | `/cashflow/<id>/edit` |
| Cashflow ↔ trade playbook | `/clients/<id>/cashflow-trade-integrity` |

---

## Future improvement (not live yet)

A proper `transaction_id` on cashflow would allow automatic in-place updates when a trade changes. Until then, **manual cashflow review after trade edits is required**.
