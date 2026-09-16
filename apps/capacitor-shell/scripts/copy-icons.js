#!/usr/bin/env node
/**
 * Copy generated app icon (from favicon.svg) into Capacitor www for offline splash fallback.
 */
const fs = require("fs");
const path = require("path");

const assetsIcon = path.join(__dirname, "..", "assets", "icon.png");
const www = path.join(__dirname, "..", "www");
const iconDir = path.join(www, "icons");

if (!fs.existsSync(assetsIcon)) {
  console.warn("Run npm run build:icon first (builds from static/favicon.svg).");
  process.exit(1);
}

if (!fs.existsSync(www)) {
  fs.mkdirSync(www, { recursive: true });
}
if (!fs.existsSync(iconDir)) {
  fs.mkdirSync(iconDir, { recursive: true });
}

fs.copyFileSync(assetsIcon, path.join(iconDir, "icon.png"));
fs.copyFileSync(assetsIcon, path.join(www, "icon.png"));
console.log("Copied assets/icon.png → www/icon.png");
