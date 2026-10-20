# Client Google Drive folders + Suitability reports

## Link a client folder

1. Create or open the client’s folder on Google Drive (**prefer a Shared drive**).
2. Share it with the **app service-account email** (shown on Client detail → Google Drive card, and Advisor → Client Drive folders). Role: **Content manager** or Editor.
3. Copy the folder ID from the URL (`…/folders/<ID>`) or paste the full URL into the app.
4. Save on **Client detail** or **Advisor → Client Drive folders**.
5. Use **Open Drive folder** to verify access in the browser (your Google login).

The service account email is `client_email` inside `service_account.json` (same file as codebase Drive backups). There is no separate env var for the email.

## Suitability report

1. Link the client folder first.
2. On Client detail → **Generate suitability report**.
3. The app builds a DOCX from `static/doc_templates/suitability_report_template.docx`, personalizes:
   - Client name
   - Preparer (logged-in advisor)
   - Asset classes from current holdings
   - Risk profile from `Client.risk_profile`, or **Moderately aggressive** if blank
4. Uploads as an editable **Google Doc** into the linked folder and shares writer access with your login email when possible.
5. Edit in Google Docs, then **Download** from the report list (exports DOCX).

## One-time migration

```bash
cd /opt/Inertia2026v1   # or local repo
./venv/bin/python3 migrations/add_client_google_drive_folder.py
```

Adds `client.google_drive_folder_id`, `client.google_drive_folder_note`, and `suitability_report`.

## Sent email archive

On **Client detail → Google Drive**, use **Sync client emails** to:

1. Pull Gmail **Sent** via IMAP (`MAIL_USERNAME` / `MAIL_PASSWORD`) for messages To/Cc the client’s primary + secondary emails  
2. Merge any gaps from MySQL `EmailLog`  
3. Store under `var/client_email_archive/client_<id>/` (index + HTML/EML)  
4. Upload new messages into an **`Emails/`** subfolder in the linked Drive (sibling of `Holdings/`, under the client name folder)

Incremental: later syncs only fetch since `last_sent_at` (with 1-day overlap). Dedupes by Message-ID / `emaillog:<id>`.

**Investigation search** walks **all nodes** under the client Drive folder (not only `Emails/`) for `.html` / `.eml` files, and also merges local archive + live EmailLog + DB recommendation sessions so a gap in one source does not hide data.

If the Drive folder is **not linked**, sync still saves locally and shows a **Drive folder missing** warning so you can add the folder ID. Holdings recon investigation surfaces the same flag.

Also available from Hub → Holdings reconciliation when a run is open.

## Trade evidence / contract notes (investigation)

For holdings recon, link a **separate** Drive folder that holds broker contract notes
(PDF), trade xlsx, and optional order-book screenshots:

1. Hub → Holdings reconciliation → **Upload / prior runs / trade evidence**
2. Paste folder URL/ID + PDF passwords (comma-separated; often client PAN(s))
3. **Refresh investigation** — app scans notes, compares to system trades + sent recos

Stored under `var/holdings_reconciliation/trade_drive_links.json` (local/server; not DB).

## Holdings statements (`Holdings/` / `Holding Statement/`)

On **Hub → Holdings reconciliation**:

1. Select a client — the app lists CSV / XLSX / PDF under **`Holdings/`**, **`Holding Statement/`**, and one nested `…/Holdings/` (skips Emails / Trade details).
2. Choose one or more to reconcile, **or** upload new files (multi-select). Each file is its own recon run.
3. New uploads go into the non-empty holdings-like folder when present; otherwise **`Holdings/`** is created.

If Drive is not linked, recon still works locally; the UI shows a link warning.

## Troubleshooting uploads

If generate fails with **storageQuotaExceeded**, the folder is on personal My Drive. Move it into a **Shared drive**, add the service account as Content manager, update the Folder ID, and retry.
