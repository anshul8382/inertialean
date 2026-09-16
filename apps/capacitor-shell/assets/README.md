# Native app icons

- **Source:** `static/favicon.svg` → `npm run build:icon` → `assets/icon.png` (1024×1024).
- **Regenerate** Android/iOS launcher + splash after favicon changes:

  ```bash
  npm run prepare:icons
  npm run sync:android
  ```

- `generate:assets` writes all `mipmap-*` / `AppIcon` / splash sizes from `icon.png`.
