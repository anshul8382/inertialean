#!/usr/bin/env node
/**
 * Build 1024×1024 app icon from static/favicon.svg (same as web favicon).
 */
const fs = require("fs");
const path = require("path");

const repoRoot = path.join(__dirname, "..", "..", "..");
const svgPath = path.join(repoRoot, "static", "favicon.svg");
const pngFallback = path.join(repoRoot, "static", "favicon-32.png");
const assetsDir = path.join(__dirname, "..", "assets");
const outPath = path.join(assetsDir, "icon.png");
const size = 1024;

async function main() {
  if (!fs.existsSync(assetsDir)) {
    fs.mkdirSync(assetsDir, { recursive: true });
  }

  if (fs.existsSync(svgPath)) {
    const sharp = require("sharp");
    await sharp(svgPath, { density: 384 })
      .resize(size, size, { fit: "contain", background: "#2563eb" })
      .png()
      .toFile(outPath);
    console.log(`Built ${outPath} from static/favicon.svg (${size}×${size})`);
    return;
  }

  if (fs.existsSync(pngFallback)) {
    const sharp = require("sharp");
    await sharp(pngFallback)
      .resize(size, size, { kernel: sharp.kernel.lanczos3 })
      .png()
      .toFile(outPath);
    console.log(`Built ${outPath} from static/favicon-32.png (${size}×${size})`);
    return;
  }

  console.error("Missing static/favicon.svg — cannot build app icon.");
  process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
