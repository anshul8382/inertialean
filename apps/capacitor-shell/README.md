# INERTIA Capacitor Shell (iOS + Android)

Native **iOS and Android apps** that load the **full web application** in a WebView. You get the same features and **role-based menu** as the desktop browser — no waiting for native Expo screens.

**Strategy:** Use this shell **now** (Option B). The Expo app in `apps/mobile/` remains the long-term **native UI** path (Option A).

---

## Prerequisites

- Node.js 18+
- Flask app running (see below)
- **iOS:** Xcode + CocoaPods (Mac only)
- **Android:** Android Studio + SDK

---

## Quick start

### 1. Start Flask (same machine)

```bash
# From repo root — default local dev
FLASK_ENV=development python3 run.py
```

For a **physical phone** on Wi‑Fi, bind all interfaces:

```bash
./scripts/run_dev_for_capacitor.sh
```

### 2. Configure the shell

```bash
cd apps/capacitor-shell
cp .env.example .env
```

Edit `.env` — **`CAP_SERVER_URL`** must match how the device reaches Flask:

| Target | `CAP_SERVER_URL` |
|--------|------------------|
| iOS Simulator | `http://127.0.0.1:5001` |
| Android Emulator | `http://10.0.2.2:5001` |
| Physical device | `http://<your-lan-ip>:5001` |
| Production | `https://your-host.com` |

### 3. Install and sync

```bash
npm install
npm run prepare:icons   # INERTIA logo → launcher icon + splash (Android/iOS)
npm run sync
```

### 4. Run on device / simulator

```bash
npm run open:ios      # Xcode → Run
npm run open:android  # Android Studio → Run
```

Or CLI:

```bash
npm run run:ios
npm run run:android
```

---

## How it works

1. Capacitor opens a native WebView pointed at `CAP_SERVER_URL`.
2. You sign in with the **normal web login** (session cookies + 2FA).
3. The web app renders the **full navbar** (filtered by role, `has_route_access`, `NAV_*` flags) plus the **mobile bottom nav**.
4. `static/js/mobile-config.js` detects Capacitor, adds `body.mobile-app`, handles Android back button, and skips the service worker (avoids stale cache in the shell).

No separate mobile menu API is required — access control is identical to the web app.

---

## Production

1. Deploy Flask behind **HTTPS**.
2. Set `CAP_SERVER_URL=https://your-production-host.com` in `.env`.
3. Set `SESSION_COOKIE_SECURE=True` on the server.
4. Run `npm run sync`, then build in Xcode / Android Studio or use [Capacitor CI](https://capacitorjs.com/docs/guides/deploying-updates).

**App icon:** Built from `static/favicon.svg` (same as browser favicon). Regenerate with `npm run prepare:icons` after changing the favicon.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Blank / offline splash | Wrong `CAP_SERVER_URL` — run `npm run sync` after editing `.env` |
| Android emulator cannot connect | Use `http://10.0.2.2:5001`, not `127.0.0.1` |
| Phone cannot connect | Flask on `0.0.0.0`, firewall allows 5001, use LAN IP in `.env` |
| Login works on web but not app | Same URL scheme (http vs https); check cookies / 2FA on web first |
| Cleartext HTTP blocked (Android 9+) | Dev uses `cleartext: true` in config; production must use HTTPS |

---

## Related

- `docs/MOBILE_STRATEGY.md` — web + Capacitor + Expo roadmap
- `apps/mobile/` — Expo native UI (Phase 1 scaffold; Option A later)
