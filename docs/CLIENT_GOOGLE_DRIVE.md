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

## Troubleshooting uploads

If generate fails with **storageQuotaExceeded**, the folder is on personal My Drive. Move it into a **Shared drive**, add the service account as Content manager, update the Folder ID, and retry.
