#!/usr/bin/env node
/** Keep favicon full-bleed on Android adaptive icons (capacitor-assets adds 16.7% inset + white bg). */
const fs = require("fs");
const path = require("path");

const res = path.join(__dirname, "..", "android", "app", "src", "main", "res");
const bgXml = path.join(res, "values", "ic_launcher_background.xml");

fs.writeFileSync(
  bgXml,
  `<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">#2563eb</color>
</resources>
`
);

const adaptive = `<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
`;

for (const name of ["ic_launcher.xml", "ic_launcher_round.xml"]) {
  const p = path.join(res, "mipmap-anydpi-v26", name);
  if (fs.existsSync(p)) {
    fs.writeFileSync(p, adaptive);
  }
}

console.log("Patched Android adaptive icon (favicon blue, no crop inset).");
