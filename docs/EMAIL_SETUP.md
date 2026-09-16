# Email setup (SMTP)

Recommendation emails, password reset, and reports use **SMTP** via `MAIL_*` variables in `.env`.

## Error: `(535, b'Incorrect authentication data')`

Gmail/Google rejected the username or password. This is **not** an app bug — fix credentials on the **server** where Flask runs (port 5003 / Gunicorn).

### Google Workspace / Gmail (`@equities4wealth.com` + `smtp.gmail.com`)

1. Sign in as the **same account** as `MAIL_USERNAME` (e.g. `anshul@equities4wealth.com`).
2. Enable **2-Step Verification** on that Google account.
3. Create an **App Password**: [Google App Passwords](https://myaccount.google.com/apppasswords) → Mail → Other → “INERTIA”.
4. In server `.env` (no quotes, no spaces in the password):

   ```env
   MAIL_SERVER=smtp.gmail.com
   MAIL_PORT=587
   MAIL_USE_TLS=True
   MAIL_USE_SSL=False
   MAIL_USERNAME=anshul@equities4wealth.com
   MAIL_PASSWORD=xxxxxxxxxxxxxxxx
   MAIL_DEFAULT_SENDER=anshul@equities4wealth.com
   ```

5. **Restart** the app after changing `.env` (systemd / Gunicorn does not reload env on its own):

   ```bash
   sudo systemctl restart inertia-app-v2026   # or your service name
   ```

6. Test on the server:

   ```bash
   cd /opt/inertia-app-v2026   # your install path
   python3 scripts/test_smtp.py
   python3 scripts/test_smtp.py --send you@example.com
   ```

**Do not use** your normal Google password in `MAIL_PASSWORD` — only an **App Password**.

### cPanel / domain mail (`@inertiainvest.in`)

If mail is hosted on the web host (not Google), use the host’s SMTP settings from cPanel → **Email Accounts** → **Connect Devices**, for example:

```env
MAIL_SERVER=mail.inertiainvest.in
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=anshul@inertiainvest.in
MAIL_PASSWORD=<email account password>
MAIL_DEFAULT_SENDER=anshul@inertiainvest.in
```

Do **not** mix Gmail SMTP with an `@inertiainvest.in` username (or vice versa).

## Checklist

| Check | |
|-------|---|
| `MAIL_USERNAME` = account that owns the app password | |
| `MAIL_DEFAULT_SENDER` = same as `MAIL_USERNAME` (for Gmail) | |
| App password is 16 characters, no spaces in `.env` | |
| `.env` is on the **server** running port 5003 | |
| Service restarted after `.env` edit | |
| `python3 scripts/test_smtp.py` prints “login succeeded” on server | |

## Where the app sends mail

- `services/email_service.py` — recommendation emails (direct SMTP using `MAIL_*`)
- `extensions.mail` / Flask-Mail — other notifications

Both read the same `MAIL_*` environment variables loaded in `wsgi.py` / `config.py`.
